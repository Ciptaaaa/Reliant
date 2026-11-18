# Migration Guard Report

## Scope
- **Migration**: `database/migrations/2026_11_16_121500_payments_method_not_null.php`
- **Engine**: MySQL 8.0.36 (InnoDB)
- **Inspected**: `migration.sql`, `schema.sql`, `application-usage.md`, `context.md`
- **Not inspected**: the legacy gateway export file (external)
- **Unknowns**: none material; the null count and row count are stated in `schema.sql`

## Verdict
**CONDITIONAL** — 84,302 rows still have a null `method`, so the `NOT NULL` change fails on deploy, and without an explicit `ALGORITHM` it can rebuild 11.2M rows under a lock.

## Findings

### F1 — HIGH — not-null-add
- **Statement**: `ALTER TABLE payments MODIFY COLUMN method VARCHAR(24) NOT NULL` in `migration.sql:2` — adds a not-null constraint, which MySQL validates against every existing row immediately
- **Evidence**:
  - `schema.sql:16` — `SELECT COUNT(*) FROM payments WHERE method IS NULL -> 84,302` out of 11,240,911 rows
  - `migration.sql:2` — no backfill precedes the change
  - `context.md:20` — adding the constraint validates existing rows at that moment and fails if any `NULL` exists
- **Impact**: the statement fails with `Invalid use of NULL value` (ERROR 1138) and the DDL is rejected; no data is lost, but the deploy cannot proceed
- **Mitigation**: backfill the 84,302 rows to an agreed sentinel in batches, then apply the constraint in a separate step; MySQL has no `NOT VALID`/`VALIDATE`, so the data must be clean first
- **Verification**: `SELECT COUNT(*) FROM payments WHERE method IS NULL` returns 0 immediately before the change
- **Approval**: none
- **Reversible**: yes

### F2 — HIGH — lock-and-rewrite
- **Statement**: `MODIFY COLUMN` without an explicit `ALGORITHM` and `LOCK` may fall back to a table copy or rebuild of 11.2M rows, holding a metadata lock and producing replica lag
- **Evidence**:
  - `migration.sql:2` — no `ALGORITHM=` or `LOCK=` clause
  - `context.md:21` — "`MODIFY COLUMN` without an explicit `ALGORITHM` may fall back to a copy/rebuild path"
  - `application-usage.md:27` — "large DDL on the primary produces replication lag", and reporting runs on the replica
- **Impact**: a rebuild of 11.2M rows blocks writes for its duration and lags the reporting replica, with no maintenance window stated
- **Mitigation**: state the algorithm explicitly (`ALGORITHM=INPLACE, LOCK=SHARED`) and confirm MySQL accepts it for this change; otherwise run the rebuild in a window
- **Verification**: `SHOW WARNINGS` after the DDL reports the algorithm actually used, and replica lag stays within its agreed bound
- **Approval**: schedule
- **Reversible**: yes

### F3 — MEDIUM — application-compatibility
- **Statement**: a live import path can still write `NULL` after the backfill, so the constraint is violated again on the next run
- **Evidence**:
  - `application-usage.md:10` — `app/Console/Commands/ImportLegacyPayments.php:71` bulk-inserts from the old gateway export, which omits `method`
  - `application-usage.md:5` — `app/Services/Checkout/CapturePayment.php:52` always sets `method`, so the checkout path is fine
- **Impact**: once the constraint exists, the legacy import fails on the first row with a missing method, undoing the value of the backfill
- **Mitigation**: update the importer to supply a method or skip the row, and deploy it before the constraint
- **Verification**: the import runs once against a database that already has the constraint without an error
- **Approval**: none
- **Reversible**: yes

## Safer migration path
1. Agree the sentinel with reporting, which tolerates `NULL` via `COALESCE(method,'unknown')` (`application-usage.md:17`).
2. Update `app/Console/Commands/ImportLegacyPayments.php:71` to supply a method or skip the row, and deploy it.
3. Backfill in batches with a `NULL` predicate so a rerun is idempotent:
   `UPDATE payments SET method = 'unknown' WHERE method IS NULL LIMIT 5000;` repeated until 0 rows are affected, watching replica lag.
4. Apply the constraint with an explicit algorithm, in a window:
   `ALTER TABLE payments MODIFY COLUMN method VARCHAR(24) NOT NULL, ALGORITHM=INPLACE, LOCK=SHARED;`
5. Verify `SHOW COLUMNS FROM payments LIKE 'method'` shows `NO` in the `Null` column.

## Rollback and recovery
- **Schema rollback**: `ALTER TABLE payments MODIFY COLUMN method VARCHAR(24) NULL;` restores the previous definition.
- **Data recovery**: the backfill overwrites `NULL` with the sentinel; export the affected ids first if the original null-ness must be auditable.
- **Application rollback**: safe — the old application version writes `NULL` again, so drop the constraint before rolling the code back.

## Pre-deploy checklist
- [ ] Sentinel agreed with reporting
- [ ] Importer updated and deployed before the constraint
- [ ] `SELECT COUNT(*) FROM payments WHERE method IS NULL` returns 0
- [ ] Backfill batched, with replica lag monitored
- [ ] Explicit `ALGORITHM`/`LOCK` confirmed by `SHOW WARNINGS`