# Migration Guard Report — PG-003

## Verdict: **BLOCK / DO NOT MERGE**

The migration will fail at apply time as written, and even in a clean-data world it is unsafe to run against a high-traffic 2.1M-row table. Two independent blockers; either one alone would stop the merge.

---

## Blocker 1 — Duplicate rows: the `ADD CONSTRAINT` will fail outright

The developer's premise ("there should be no duplicates") is contradicted by the recorded data facts in `schema.sql`:

> `--   SELECT count(*) FROM ( SELECT email FROM users GROUP BY email HAVING count(*) > 1 ) d;  -> 1,205   -- exact duplicates`

> `--   SELECT count(*) FROM ( SELECT email FROM users GROUP BY lower(email) HAVING count(*) > 1 ) d;  -> 1,437   -- case-insensitive duplicates`

> `-- The nightly dedupe job (bin/dedupe_users.py) has not finished; 412 duplicate groups remain as of 2026-11-16.`

There are at least 1,205 exact-duplicate groups (and 412 unresolved as of the latest report). A plain `UNIQUE (email)` requires *exact* uniqueness, so every one of those 1,205 groups causes an error:

```
ERROR: could not create unique index "users_email_key"
DETAIL: Key (email)=(...) is duplicated.
```

The migration aborts; nothing is partially applied, but the deploy fails.

## Blocker 2 — Locking: `ACCESS EXCLUSIVE` on a high-write 2.1M-row table

Even if the data were clean:

```sql
ALTER TABLE users ADD CONSTRAINT users_email_key UNIQUE (email);
```

PostgreSQL implements this by building a unique index while holding an `ACCESS EXCLUSIVE` lock on `users` for the entire build. Per the case:

> **Table**: `users`, ~2.1M rows, high write traffic.
> **API**: a monolith with 6 replicas behind a load balancer.

During the build, all reads *and* writes to `users` block — including:

- `accounts/views.py:48` — `User.objects.create_user(...)` in the request handler
- `auth_backends.py:12` — `User.objects.get(email__iexact=email)` on every login
- `accounts/management/commands/import_customers.py:31` — weekly `bulk_create`

A build of this size at high traffic will stall the request path and, if it exceeds any statement/client timeout, may even be killed mid-build. This is a `HIGH`-severity availability risk on its own.

---

## Additional issue — the proposed constraint doesn't match the app's semantics

The login lookup is case-insensitive:

> `auth_backends.py:12` — `User.objects.get(email__iexact=email)` — login lookup is case-insensitive. Two rows differing only in case would raise `MultipleObjectsReturned` at login.

A `UNIQUE (email)` constraint permits `Alice@x.com` and `alice@x.com` as distinct rows. Once the duplicate pre-condition is cleared, the app would still be able to create case-variant duplicates and hit `MultipleObjectsReturned`. The intended invariant is **case-insensitive uniqueness**, which the proposed exact `UNIQUE` does not enforce.

Also note the importer: `import_customers.py:31` uses `bulk_create(..., ignore_conflicts=False)`, so any existing/colliding row will raise `IntegrityError` and abort the weekly import unless the command handles it — worth confirming before enforcing uniqueness.

---

## Required remediation (all steps)

**1. Deduplicate first, and verify clean.** The dedupe job is "not yet reported clean."

```sql
-- Identify remaining exact-duplicate groups
SELECT email, count(*) FROM users GROUP BY email HAVING count(*) > 1;
-- Identify case-insensitive groups (must also be resolved)
SELECT lower(email), count(*), array_agg(id) FROM users GROUP BY lower(email) HAVING count(*) > 1;
```

Resolve them (keep lowest `id`, update references, delete the rest — or merge after review of `name`/`created_at`). Gate the migration on these queries returning zero rows. Do not merge until the dedupe job has reported clean.

**2. Build the unique index concurrently, outside the migration lock.**

```sql
CREATE UNIQUE INDEX CONCURRENTLY users_email_key_idx ON users (lower(email));
```

`CONCURRENTLY` avoids `ACCESS EXCLUSIVE` on the table (brief lock only at start/end). It cannot run inside a transaction block, so it must be a separate, non-transactional step (in Django, wrap with a migration `RunSQL` marked `atomic = False`, or run it out-of-band from the deploy).

**3. Attach the constraint using the index** (this avoids rebuilding):

```sql
ALTER TABLE users ADD CONSTRAINT users_email_key UNIQUE USING INDEX users_email_key_idx;
```

This takes only a brief lock. Because the index is on `lower(email)`, the constraint enforces case-insensitive uniqueness, matching `auth_backends.py` and normalising the importer's input.

If for some reason exact-case uniqueness is truly desired, the index would be on `(email)` directly — but then the login `__iexact` lookup still needs a separate guard, and I'd still require step 2's concurrent build.

**4. Verify.** After attach:

```sql
SELECT conname, pg_get_constraintdef(oid) FROM pg_constraint WHERE conrelid = 'users'::regclass;
SELECT indexname, indexdef FROM pg_indexes WHERE tablename = 'users' AND indexname LIKE 'users_email%';
```

Confirm the old `users_email_idx` non-unique index can be dropped afterwards.

**5. Keep the application check but fix the race properly.** The `forms.py` `clean_email` is a check-then-act race across 6 replicas:

> `accounts/forms.py:22` — `if User.objects.filter(email=email).exists(): raise ValidationError(...)` → "a `SELECT` followed by an `INSERT`: a classic check-then-act race."

The DB constraint closes the race. The form validation remains as UX nicety; catch `IntegrityError` on `create_user` and translate it to a user-facing error.

---

## Summary table

| Concern | Evidence | Severity | Status |
|---|---|---|---|
| Exact duplicates present | `schema.sql`: 1,205 groups; 412 unresolved | BLOCKER | Must dedupe before merge |
| `ACCESS EXCLUSIVE` on 2.1M-row high-write table | `ALTER TABLE ... ADD CONSTRAINT UNIQUE`; high write traffic | HIGH | Use `CREATE INDEX CONCURRENTLY` + `ADD CONSTRAINT USING INDEX` |
| Case-insensitive semantics not enforced | `auth_backends.py:12` `email__iexact`; 1,437 case-dupes | HIGH | Index on `lower(email)` |
| Importer `ignore_conflicts=False` | `import_customers.py:31` | MEDIUM | Confirm `IntegrityError` handling |
| App-level validation race | `forms.py:22` | LOW (after constraint) | DB constraint fixes it; keep form check for UX |

**Verdict: BLOCK.** Do not merge `0042_users_email_unique.sql` as written. It fails on current data (duplicates) and is unsafe regardless (table lock). Replace with the dedupe-verify → `CREATE UNIQUE INDEX CONCURRENTLY (lower(email))` → `ADD CONSTRAINT ... USING INDEX` sequence, and do not merge until the dedupe job reports clean.