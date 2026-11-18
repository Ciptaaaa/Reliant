# Migration Guard Report — PG-003

## Verdict: **BLOCKER** — do not merge as written

The migration will fail outright on the current data, and even after the data is deduplicated it takes an `ACCESS EXCLUSIVE` lock on a large, high-traffic table during the build. Both issues must be fixed.

---

## Findings

### 1. BLOCKER — Duplicate rows will make `ADD CONSTRAINT UNIQUE` fail

The migration is:

```sql
ALTER TABLE users ADD CONSTRAINT users_email_key UNIQUE (email);
```

This builds a unique index over existing data. PostgreSQL rejects the statement if duplicates exist. They do:

> `SELECT email FROM users GROUP BY email HAVING count(*) > 1` → **1,205 — exact duplicates**

The proposed constraint is on exact `email`, so 1,205 duplicates are enough to fail it. The application-level validation the developer cites does not change this:

> `accounts/forms.py:22` → "a `SELECT` followed by an `INSERT`: a classic check-then-act race; it does not prevent duplicates under concurrency across 6 replicas."

And the developer's own claim that "there should be no duplicates" is contradicted by the schema facts:

> "The nightly dedupe job (`bin/dedupe_users.py`) has not finished; **412 duplicate groups remain** as of 2026-11-16."

Even if the counting differed slightly, the "not yet reported clean" status in `context.md` means the precondition is not met.

**Additional risk — the constraint matches the wrong semantics.** The only login path is case-insensitive:

> `auth_backends.py:12` → `User.objects.get(email__iexact=email)`

> "Two rows differing only in case would raise `MultipleObjectsReturned` at login."

A unique index on exact `email` leaves **1,437 case-insensitive duplicate groups** (vs. 1,205 exact) intact. That means sign-in can still break after the migration "succeeds." The constraint ideally should be on `lower(email)` (or the lookup made exact), and the data must be deduplicated on that same key first.

### 2. HIGH — `ADD CONSTRAINT UNIQUE` takes `ACCESS EXCLUSIVE` during a 2.1M-row index build

As quoted in `context.md`:

> "even without duplicates an unqualified `ADD CONSTRAINT UNIQUE` takes `ACCESS EXCLUSIVE` while it builds the index on 2.1M rows (`HIGH`)."

`ACCESS EXCLUSIVE` blocks reads and writes. On a table with "high write traffic" behind 6 replicas, the build will stall query traffic and can back up the load balancer. This is not a safe deploy step for a live system.

---

## Required changes before merge

1. **Deduplicate on the key you will enforce.** Since login uses `email__iexact`, resolve all **1,437** case-insensitive groups (not just the 1,205 exact ones), then confirm the dedupe reports clean. Decide the canonical casing (the import already normalises to `email.lower()`; production writes via `create_user` do not, so fix that path too).

2. **Use the concurrent-index path** to avoid the `ACCESS EXCLUSIVE` build:
   - Create the index without a table rewrite: `CREATE UNIQUE INDEX CONCURRENTLY users_email_key ON users (lower(email));` (run outside a transaction; `CONCURRENTLY` can fail and leave an invalid index — verify with `pg_index.indisvalid`).
   - Attach it as the constraint: `ALTER TABLE users ADD CONSTRAINT users_email_key UNIQUE USING INDEX ...;`
   - Note that `CONCURRENTLY` on PG 15 still permits concurrent writers, which is what the rolling deploy needs.

3. **Then retire the check-then-act in the form.** Keep the form validation for UX, but rely on the DB constraint for correctness, and catch the `IntegrityError` in `accounts/views.py:48` and `import_customers.py:31` (the weekly `bulk_create` with `ignore_conflicts=False` will start failing on partner-sourced duplicates — that needs explicit handling).

4. **Verify out-of-repo writers.** The analytics warehouse does direct SQL and is "not in this repository." Confirm it can tolerate the constraint (or is fixed) before rollout, since it is an unverified source of duplicates.

---

## Summary

| Check | Result |
|---|---|
| Duplicate precondition met | ❌ 1,205 exact / 1,437 case-insens. remain; dedupe not clean |
| Constraint matches login semantics | ❌ case-insensitive login, exact constraint |
| Locking safe for production | ❌ `ACCESS EXCLUSIVE` on 2.1M-row live table |
| **Overall** | **BLOCKER** |

**Merge this? No.** Fix the data and use the `CREATE UNIQUE INDEX CONCURRENTLY` → `USING INDEX` path, on `lower(email)`, and re-run Migration Guard.