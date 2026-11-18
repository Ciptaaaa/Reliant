# Migration Guard Report

## Scope
- **Migration**: `db/migrations/0042_users_email_unique.sql`
- **Engine**: PostgreSQL 15
- **Inspected**: `migration.sql`, `schema.sql`, `application-usage.md`, `context.md`
- **Not inspected**: direct SQL from the analytics warehouse (not in this repository)
- **Unknowns**: the nightly dedupe job reports 412 duplicate groups remaining as of 2026-11-16, while the data facts show 1,437 case-insensitive groups; the two numbers cannot both be current

## Verdict
**BLOCKED** — 1,437 case-insensitive duplicate groups make the unique constraint fail outright, and the build would lock 2.1M rows even if the data were clean.

## Findings

### F1 — BLOCKER — unique-constraint
- **Statement**: `ALTER TABLE users ADD CONSTRAINT users_email_key UNIQUE (email)` in `migration.sql:2` — builds a unique index, which fails if any duplicate key exists
- **Evidence**:
  - `schema.sql:15` — `SELECT count(*) FROM users GROUP BY email HAVING count(*) > 1 -> 1,205` exact duplicate groups
  - `schema.sql:12` — `GROUP BY lower(email) HAVING count(*) > 1 -> 1,437` case-insensitive duplicate groups
  - `migration.sql:2` — the constraint is added with no dedupe step, and it is case-sensitive while the login lookup is not (`application-usage.md:24`)
- **Impact**: the build aborts with `could not create unique index ... Key (email)=(...) is duplicated`, the deploy transaction rolls back and the constraint is never created; no data is lost because the statement fails cleanly
- **Mitigation**: dedupe first, and decide whether uniqueness is case-insensitive; the login uses `email__iexact`, so a case-sensitive constraint would leave two-cases-of-one-address rows able to raise `MultipleObjectsReturned` at login
- **Verification**: both duplicate queries return 0; the login path at `auth_backends.py:12` no longer risks `MultipleObjectsReturned`
- **Approval**: none
- **Reversible**: yes

### F2 — HIGH — lock-and-rewrite
- **Statement**: an unqualified `ADD CONSTRAINT ... UNIQUE` takes `ACCESS EXCLUSIVE` on `users` while it builds the index across 2,104,882 rows, blocking all reads and writes
- **Evidence**:
  - `migration.sql:2` — a single `ALTER TABLE`, no `CONCURRENTLY` and no `lock_timeout`
  - `schema.sql:5` — `SELECT count(*) FROM users -> 2,104,882`
  - `context.md:12` — high write traffic, six replicas behind a load balancer
- **Impact**: the table is unavailable for the whole index build, and with high write traffic the queue behind the lock grows with every request
- **Mitigation**: build the index concurrently and attach it:
  ```sql
  CREATE UNIQUE INDEX CONCURRENTLY users_email_key ON users (email);
  ALTER TABLE users ADD CONSTRAINT users_email_key UNIQUE USING INDEX users_email_key;
  ```
  Run outside a transaction block, in a session with `lock_timeout` set
- **Verification**: `pg_index` shows `indisvalid = true` and `indisready = true`; no `ACCESS EXCLUSIVE` wait appears in `pg_locks` during the build
- **Approval**: schedule
- **Reversible**: yes

### F3 — MEDIUM — application-compatibility
- **Statement**: the application's own duplicate check is a check-then-act race, so imports and concurrent signups fail once the constraint exists unless the write path changes first
- **Evidence**:
  - `application-usage.md:5` — `accounts/forms.py:22` does `filter(email=email).exists()` then inserts, which races across six replicas
  - `application-usage.md:18` — `accounts/management/commands/import_customers.py:31` uses `bulk_create` with `ignore_conflicts=False`
- **Impact**: after the constraint exists, the weekly import aborts on the first conflict and concurrent signups surface a raw `IntegrityError` instead of the form message
- **Mitigation**: normalise stored email case on write, handle `IntegrityError` in `forms.py:22`, and make the import conflict-tolerant; deploy before the constraint
- **Verification**: the import completes without an integrity error against a database that already has the constraint
- **Approval**: none
- **Reversible**: yes

## Safer migration path
1. Decide the semantics: the login uses `email__iexact`, so normalise to lowercase and constrain that column.
2. Resolve duplicates: keep the lowest `id` per `lower(email)` group, merge or delete the rest, logging every deletion and exporting the rows first.
3. Fix the writers: `accounts/forms.py:22` catches `IntegrityError`; `import_customers.py:31` normalises casing and tolerates conflicts. Deploy.
4. Build without blocking:
   ```sql
   CREATE UNIQUE INDEX CONCURRENTLY users_email_key ON users (email);
   ALTER TABLE users ADD CONSTRAINT users_email_key UNIQUE USING INDEX users_email_key;
   ```
5. Verify both duplicate queries return 0 and the weekly import has run once successfully.

## Rollback and recovery
- **Schema rollback**: `ALTER TABLE users DROP CONSTRAINT users_email_key;` is clean; the concurrently built index can be dropped with `DROP INDEX CONCURRENTLY`.
- **Data recovery**: the dedupe deletes rows and that cannot be undone without a backup; export every duplicate row before deleting it.
- **Application rollback**: safe — the old application version does not depend on the constraint.

## Pre-deploy checklist
- [ ] Duplicate semantics decided (case-sensitive or case-insensitive)
- [ ] Both duplicate queries return 0
- [ ] Duplicate rows exported before deletion
- [ ] Writers handle the conflict
- [ ] Index built with `CONCURRENTLY` outside a transaction