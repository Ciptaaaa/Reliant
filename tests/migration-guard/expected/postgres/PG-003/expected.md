# PG-003 — Expected analysis

## Expected verdict

**BLOCKED** — duplicates still exist, so the constraint fails; and the statement as written would
block the table for the whole index build on 2.1M rows.

## Findings

### F1 — `BLOCKER` — `unique-constraint` — `UNIQUE (email)` fails on existing duplicates

`schema.sql` records 1,205 exact duplicate groups and 1,437 case-insensitive duplicates (the login
path is `email__iexact`, so the case-insensitive count is the one that matters for correctness).
`ALTER TABLE users ADD CONSTRAINT … UNIQUE (email)` builds the index and fails with
`could not create unique index "users_email_key" / Key (email)=(...) is duplicated`. The migration
cannot be applied as written.

**Evidence required:** `schema.sql` (duplicate counts), `migration.sql:2`,
`application-usage.md` (login uses `iexact`, so case variants are duplicates from the product's point
of view).

**Mitigation:**

1. Resolve duplicates first: merge or rename rows (this is a **data change**, needs a human decision
   about which account survives).
2. Add the constraint on the correct expression; if uniqueness must be case-insensitive, use
   `UNIQUE (lower(email))` or `citext` instead of plain `UNIQUE (email)`.
3. Then use the online path: `CREATE UNIQUE INDEX CONCURRENTLY users_email_key ON users (email);`
   followed by `ALTER TABLE users ADD CONSTRAINT users_email_key UNIQUE USING INDEX users_email_key;`.
4. Prevent new duplicates **before** the constraint exists: the `clean_email` check is a race. Add the
   index in a separate step and deploy the code that handles `IntegrityError` at the same time.

**Approval: none (F1)** — the statement is refused by the engine and rolls back, so no data is lost and no one has to accept any. It is still a BLOCKER because the deploy cannot succeed until the duplicates are resolved. The data change that resolves them (merge or rename rows) is where a human decision is required.

### F2 — `HIGH` — `lock-and-rewrite` — index build under `ACCESS EXCLUSIVE`

`ADD CONSTRAINT … UNIQUE` takes `ACCESS EXCLUSIVE` and builds the index inline. On 2.1M rows under
high write traffic, every read and write on `users` waits. Even after duplicates are cleaned, the
statement as written is a write outage.

**Evidence required:** `migration.sql:2`, row count in `schema.sql`.

**Mitigation:** the two-step `CONCURRENTLY` + `USING INDEX` path above; the `CONCURRENTLY` build must
run outside a transaction block.

### F3 — `MEDIUM` — `application-compatibility` — bulk import will start failing

`accounts/management/commands/import_customers.py:31` does `bulk_create(..., ignore_conflicts=False)`.
Once the unique index exists, any duplicate email from the partner CSV aborts the whole import. The
weekly import must either dedupe its input or use `ignore_conflicts=True` (with the consequence
recorded).

**Evidence required:** `application-usage.md` (import command).

### F4 — `INFO` — `no-risk-observed` — duplicate detection is possible without locking

The verification queries (`GROUP BY … HAVING count(*) > 1`) are read-only and can run on a replica.

## Unknowns that must be reported

- The weekly import is scheduled out-of-band; whether it runs during the migration window is unknown.
- Which duplicate surviving row is correct is not observable from the repository.

## Verdict reasoning

Duplicate rows plus a unique constraint → evidence of certain failure → `BLOCKER` → `BLOCKED`.
The `HIGH` about locking does not change the verdict. A report that only mentions locking and ignores
duplicates has missed the critical finding.
