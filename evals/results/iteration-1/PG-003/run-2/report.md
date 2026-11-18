# Migration Guard Report — PG-003

**File:** `db/migrations/0042_users_email_unique.sql`
**Statement:** `ALTER TABLE users ADD CONSTRAINT users_email_key UNIQUE (email);`

## Verdict

**BLOCKER — do not merge as written.**

Two independent failures, and the migration is unsafe *even after* duplicates are cleaned:

1. **It will fail now.** The table provably contains duplicates, so `ADD CONSTRAINT ... UNIQUE` aborts on the first offending group. This is not a risk; it is a certainty given the data facts.
2. **It takes `ACCESS EXCLUSIVE`.** Even with clean data, the unqualified form locks the table exclusively while building the index on ~2.1M rows under high write traffic.
3. **The proposed key is also wrong.** `UNIQUE (email)` is case-sensitive, but login is case-insensitive, so it does not actually prevent the failure mode the developer is trying to fix.

---

## Evidence

### 1. Duplicate rows exist right now → constraint will fail (BLOCKER)

From `schema.sql`, data facts collected 2026-11-16:

> `--   SELECT count(*) FROM ( SELECT email FROM users GROUP BY email HAVING count(*) > 1 ) d;  -> 1,205   -- exact duplicates`

> `-- The nightly dedupe job (bin/dedupe_users.py) has not finished; 412 duplicate groups remain as of 2026-11-16.`

And from `context.md`:

> "There is a nightly dedupe script that ran for the first time last week; it is not yet reported clean."

The constraint is on the exact `email` column, and 1,205 exact-duplicate groups remain (at minimum 412). PostgreSQL validation of `UNIQUE` scans the table and will error with `duplicate key value violates unique constraint`, leaving the table locked for the duration of the failed scan. **The developer's premise is disproven by their own pasted numbers:**

> "The app already validates emails in the form, so there should be no duplicates."

`forms.py` is a check-then-act `SELECT`→`INSERT`, which explains exactly why duplicates exist. Validation in the app is not a substitute for a constraint.

### 2. Lock profile — `ACCESS EXCLUSIVE` on 2.1M rows (HIGH)

`ALTER TABLE ... ADD CONSTRAINT ... UNIQUE` without a pre-built index acquires `ACCESS EXCLUSIVE` and holds it while the unique index is built. On a table of 2.1M rows with high write traffic across 6 replicas, this blocks all reads and writes for the build duration (second-to-minute scale; longer under IO pressure). Deploying this as a rolling migration will stall live traffic.

### 3. Wrong key semantics: `UNIQUE (email)` ≠ case-insensitive uniqueness (design defect)

The app normalises only via `email.lower()` in the importer, while the login path is case-insensitive:

> `auth_backends.py:12` — `User.objects.get(email__iexact=email)` — login lookup is case-insensitive. "Two rows differing only in case would raise `MultipleObjectsReturned` at login."

> `-- ... GROUP BY lower(email) HAVING count(*) > 1 ... -> 1,437   -- case-insensitive duplicates`

A case-sensitive unique constraint permits `Alice@x.com` and `alice@x.com` to coexist. That is exactly what triggers `MultipleObjectsReturned` at login. The constraint as written does **not** fix the reported bug; the correct key is on `lower(email)` (or a normalised column).

### 4. Unknown dependencies (assumption, not verified)

> "Any direct SQL from the analytics warehouse; not in this repository."

Upsert paths that rely on `ON CONFLICT` or that write mixed-case addresses (imports, direct warehouse SQL) may start failing once a constraint exists. Read paths or batch jobs that assume duplicates are allowed will need auditing before enforcement. This is not a blocker on its own but must be resolved before rollout.

---

## Why the developer's diagnosis is right but the fix is wrong

The race in `forms.py` is real:

> "a `SELECT` followed by an `INSERT`: a classic check-then-act race; it does not prevent duplicates under concurrency across 6 replicas."

A database constraint is the correct remedy for that race. The implementation in `migration.sql` is not.

---

## Required changes before merge

1. **Clean up data first.** Finish the dedupe to zero duplicate groups (both exact and case-insensitive), and re-verify with the same queries before proceeding. Do not merge until these return 0:
   ```sql
   SELECT count(*) FROM (SELECT email FROM users GROUP BY lower(email) HAVING count(*) > 1) d;
   ```
2. **Use the case-insensitive key.** Enforce `UNIQUE` on `lower(email)` (e.g. a functional unique index or a `citext`/normalised column), not bare `email`. Optionally ALSO keep `email` normalised on write.
3. **Build concurrently, then attach.** Avoid `ACCESS EXCLUSIVE` for the build:
   ```sql
   -- Prerequisite: duplicates = 0, verified above.
   CREATE UNIQUE INDEX CONCURRENTLY users_email_lower_uk ON users (lower(email));
   ```
   Then in a **separate, short transaction** (which will still take a brief `ACCESS EXCLUSIVE` but only to attach the pre-built index):
   ```sql
   ALTER TABLE users
     ADD CONSTRAINT users_email_lower_key UNIQUE USING INDEX users_email_lower_uk;
   ```
   (Django: use `AddConstraint` with a `UniqueConstraint(Lower("email"), name=...)` in `Meta.constraints`, and run `CREATE INDEX CONCURRENTLY` out-of-band via a manual migration with `atomic = False`, or `sqlmigrate` reviewed by hand.)
4. **Handle the expected application errors.** `create_user` and `bulk_create` (`ignore_conflicts=False`) must catch `IntegrityError` and translate it to the same `ValidationError` the form raises, otherwise concurrent signups will surface as HTTP 500s. The importer (`import_customers.py`) must handle the new conflict path (or use `ignore_conflicts=True`/`ON CONFLICT DO NOTHING` deliberately).
5. **Resolve unknown writers.** Confirm the warehouse SQL and any other non-repo writers are compatible before enforcement.

## Rollback consideration

`CREATE INDEX CONCURRENTLY` can be dropped with `DROP INDEX CONCURRENTLY`; the attached constraint with `ALTER TABLE ... DROP CONSTRAINT`. Leaving the non-unique `users_email_idx` in place during transition is harmless and can be dropped later once the new index covers lookups.

---

**Summary:** The migration does not fix the reported bug (case-insensitive duplicates survive), it will fail outright on current data (1,205 exact duplicate groups remain), and even on clean data it takes `ACCESS EXCLUSIVE` on a 2.1M-row hot table. Reject and rework using the concurrent functional-unique path above after dedupe completes.