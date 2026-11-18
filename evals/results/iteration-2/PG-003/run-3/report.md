# Migration Guard Report — PG-003

## Verdict

**🚫 BLOCKED — do not merge as written.** Two independent reasons, either of which alone is sufficient:

1. **BLOCKER:** The migration will fail on execution. The table contains duplicates; `UNIQUE` cannot be added over them.
2. **HIGH:** Even after duplicates are resolved, an unqualified `ALTER TABLE ... ADD CONSTRAINT ... UNIQUE` acquires `ACCESS EXCLUSIVE` for the full duration of an index build on ~2.1M rows, blocking reads and writes on a high-write-traffic table.

A safe path exists (see "Required changes"), but it is a multi-step, multi-deploy change, not a one-line migration.

---

## Evidence reviewed

### 1. Duplicates exist in production data (BLOCKER)

From `schema.sql`, developer-pasted facts on 2026-11-16:

> ```
> SELECT count(*) FROM (
>   SELECT email FROM users GROUP BY email HAVING count(*) > 1
> ) d;                                                          -> 1,205   -- exact duplicates
> ```

The constraint being added is `UNIQUE (email)` — **exact** match, not case-insensitive. There are **1,205 exact duplicate email values** across 1,437 case-insensitive groups. PostgreSQL will reject the `ALTER` with `could not create unique index ... Key (email)=(...) is duplicated`.

The developer's belief that "the app already validates emails in the form, so there should be no duplicates" is contradicted by:

- `application-usage.md`: the form check is check-then-act —
  > a `SELECT` followed by an `INSERT`: a classic check-then-act race; it does not prevent duplicates under concurrency across 6 replicas.
- The dedupe job is incomplete:
  > The nightly dedupe job (`bin/dedupe_users.py`) has not finished; 412 duplicate groups remain as of 2026-11-16.
- `import_customers.py:31` uses `bulk_create(..., ignore_conflicts=False)` with casing normalised only on `email.lower()`, i.e. it can insert exact duplicates from the partner CSV every week.

### 2. The migration is a blocking DDL operation (HIGH)

`migration.sql`:
```sql
ALTER TABLE users ADD CONSTRAINT users_email_key UNIQUE (email);
```

This form builds the unique index inline while holding a table-level `ACCESS EXCLUSIVE` lock. On a 2.1M-row table with "high write traffic" and "6 replicas behind a load balancer", this blocks all readers and writers for the duration of the build (load-dependent, but commonly multi-second to tens of seconds at this size on a busy table), causing request latency spikes and potential timeouts across all replicas. The existing `users_email_idx` (non-unique, same column) does not make this cheap — PostgreSQL still builds a new index.

---

## Required changes

### Step A — Reconcile duplicates *before* the constraint (separate deploy)

1. Let `bin/dedupe_users.py` finish and report clean **for exact duplicates**. Until it does, do not proceed. 412 duplicate groups were still open at last report.
2. Production must verify:
   ```sql
   SELECT count(*) FROM (
     SELECT email FROM users GROUP BY email HAVING count(*) > 1
   ) d;   -- must return 0
   ```
3. Decide the **semantic** of uniqueness:
   - The app's login lookup is `User.objects.get(email__iexact=email)` (`auth_backends.py:12`), which is case-insensitive. A case-sensitive DB constraint (`UNIQUE (email)`) still permits `Alice@x.com` / `alice@x.com` — two rows that would make login raise `MultipleObjectsReturned`. If case-insensitive uniqueness is intended, the constraint must be on `lower(email)` (or `citext`), and the 1,437 case-insensitive groups must be resolved first. **Clarify intended semantics with the product owner before implementing.**
4. Note the unresolved risk flagged in `application-usage.md`:
   > Any direct SQL from the analytics warehouse; not in this repository.
   Dedupe and verification must also account for rows inserted by that path, or the constraint may still fail at creation time.

### Step B — Build the unique index concurrently (separate deploy, non-blocking)

Do **not** add a constraint directly. Instead:

```sql
-- db/migrations/00xx_users_email_unique_idx_concurrently.sql
CREATE UNIQUE INDEX CONCURRENTLY users_email_key_idx ON users (email);
```

or, if case-insensitive uniqueness is the intended semantics:

```sql
CREATE UNIQUE INDEX CONCURRENTLY users_email_lower_key_idx ON users (lower(email));
```

`CREATE INDEX CONCURRENTLY` does not take `ACCESS EXCLUSIVE`; it takes weaker locks that do not block normal reads/writes. Caveats to plan for:

- It cannot run inside a transaction block. With Django, use a migration with `atomic = False` and `RunSQL(..., atomic=False)`.
- If it fails (e.g. a concurrent duplicate is inserted mid-build), it leaves an `INVALID` index that must be dropped and rebuilt. Re-verify duplicates immediately before running.
- During the build, application writes can still insert duplicates because there is no enforced constraint yet; the build will then fail as `INVALID`. Coordinate with Step A and consider a brief write freeze or serialize writes during the window.

### Step C — Attach as a constraint (separate deploy, non-blocking when using the pre-built index)

Optional but recommended so the constraint is visible as a real constraint (and Django introspection sees it):

```sql
-- db/migrations/00xx_users_email_unique_constraint.sql
ALTER TABLE users
  ADD CONSTRAINT users_email_key UNIQUE USING INDEX users_email_key_idx;
```

When an existing unique index is supplied via `USING INDEX`, PostgreSQL does not rebuild it, so the `ACCESS EXCLUSIVE` lock is held only briefly (metadata update) rather than for the full index build. It still briefly blocks, so run it in a low-traffic window and set a short `lock_timeout`.

### Step D — Close the application race permanently

The DB constraint does not remove the need to handle the race in the app; it makes the race raise `IntegrityError` instead of silently creating a duplicate. Update `accounts/views.py:48` / `forms.py:22` to catch `IntegrityError` on `create_user` and return a proper validation error. Also fix `import_customers.py:31` to use `ignore_conflicts=True` (or `ON CONFLICT DO NOTHING`) once the constraint exists, and normalise casing consistently.

---

## Summary

| Check | Result |
|---|---|
| Will the migration succeed against current data? | **No** — 1,205 exact duplicate email values (`BLOCKER`) |
| Does the migration block readers/writers? | **Yes** — inline `UNIQUE` build takes `ACCESS EXCLUSIVE` on 2.1M rows (`HIGH`) |
| Does it match intended login semantics? | **Unclear** — login is `iexact`, constraint is exact; must decide before merging |
| Safe path provided? | Yes — dedupe to zero, `CREATE UNIQUE INDEX CONCURRENTLY`, `UNIQUE USING INDEX`, then app-side `IntegrityError` handling |

**Verdict: BLOCKED.** The one-line migration must not be merged. The constraint is a correct goal, but it needs duplicate reconciliation across (at minimum) two deploys, a concurrent index build, and app-side error handling before it can be safely applied to production.