# Migration Guard Report

## Scope
- **Migration**: `prisma/migrations/20261116091500_orders_status_not_null/migration.sql`
- **Engine**: PostgreSQL 15
- **Inspected**: `migration.sql`, `schema.sql`, `application-usage.md`, `context.md`
- **Not inspected**: the finance reporting queries (outside this repository), the deployed fulfilment worker version
- **Unknowns**: whether the fulfilment worker runs the same release as the API; if it lags, new orders stay `NULL` longer than the API expects

## Verdict
**CONDITIONAL** — 36,117 rows still have `status IS NULL`, so `SET NOT NULL` fails on deploy, and the statement takes `ACCESS EXCLUSIVE` on 4.8M rows with no maintenance window.

## Findings

### F1 — HIGH — not-null-add
- **Statement**: `ALTER TABLE orders ALTER COLUMN status SET NOT NULL` in `migration.sql:2` — adds a not-null constraint, validated against every existing row
- **Evidence**:
  - `schema.sql:18` — `SELECT count(*) FROM orders WHERE status IS NULL -> 36,117` out of 4,812,004 rows
  - `migration.sql:2` — the statement runs with no prior backfill
  - `context.md:16` — the deploy step applies the migration inside a single transaction
- **Impact**: PostgreSQL rejects the statement with `column "status" of relation "orders" contains null values`, the deploy transaction rolls back, and the migration cannot ship as written; no data is lost
- **Mitigation**: backfill the 36,117 rows to an agreed sentinel, then add a `CHECK (status IS NOT NULL) NOT VALID`, `VALIDATE` it separately, and only then `SET NOT NULL` and drop the check
- **Verification**: `SELECT count(*) FROM orders WHERE status IS NULL` returns 0 before the constraint is added; the check constraint shows `convalidated = true` in `pg_constraint`
- **Approval**: none
- **Reversible**: yes

### F2 — MEDIUM — lock-and-rewrite
- **Statement**: `SET NOT NULL` takes `ACCESS EXCLUSIVE` on `orders` and scans all 4,812,004 rows to prove there are no nulls
- **Evidence**:
  - `migration.sql:2` — no `lock_timeout` and no `NOT VALID`/`VALIDATE` split
  - `schema.sql:17` — `SELECT count(*) FROM orders -> 4,812,004`, at ~400 inserts per minute at peak per `context.md:12`
  - `context.md:15` — no maintenance window; the migration runs automatically as part of the deploy
- **Impact**: reads and writes on `orders` block for the length of the scan and every queued query waits behind the lock, with no window to absorb it
- **Mitigation**: split into `ADD CONSTRAINT ... NOT VALID` (brief lock) and `VALIDATE CONSTRAINT` (takes `SHARE UPDATE EXCLUSIVE`, writes continue), then a short `SET NOT NULL` on the validated constraint
- **Verification**: inserts on `orders` continue during `VALIDATE`; `pg_stat_activity` shows no long `ACCESS EXCLUSIVE` wait
- **Approval**: schedule
- **Reversible**: yes

### F3 — MEDIUM — application-compatibility
- **Statement**: the constraint also rejects the writers that still insert a null `status`, so it must be sequenced after a code change
- **Evidence**:
  - `application-usage.md:15` — `src/domain/orders/create.ts:18` inserts `status: null` until the fulfilment worker sets it
  - `application-usage.md:18` — `src/imports/legacy-shop.ts:12` inserts rows without `status`, and the importer is not retired
  - `application-usage.md:9` — `src/api/orders/report.ts:41` groups by `status` with a `null` bucket the finance dashboard depends on
- **Impact**: once the constraint exists, order creation and the weekly legacy import fail, and the finance report silently mixes `NULL` with the sentinel value
- **Mitigation**: ship the default status and the null-safe importer first, deploy, confirm no new nulls for a full cycle, then apply the constraint; agree the sentinel with finance
- **Verification**: `SELECT count(*) FROM orders WHERE status IS NULL` stays 0 across a peak hour after the code change
- **Approval**: none
- **Reversible**: yes

## Safer migration path
1. Agree the sentinel with finance, who currently read `NULL` as "unknown" (`application-usage.md:9`).
2. Deploy `src/domain/orders/create.ts:18` and `src/imports/legacy-shop.ts:12` writing a real status; confirm no new nulls for one cycle.
3. Backfill in batches on the primary with an idempotent predicate:
   `UPDATE orders SET status = 'unknown' WHERE status IS NULL LIMIT 5000;` repeated until 0 rows are affected, watching replica lag.
4. Add and validate the constraint without a full-table lock:
   ```sql
   ALTER TABLE orders ADD CONSTRAINT orders_status_not_null CHECK (status IS NOT NULL) NOT VALID;
   ALTER TABLE orders VALIDATE CONSTRAINT orders_status_not_null;
   ```
5. Promote in a short step: `ALTER TABLE orders ALTER COLUMN status SET NOT NULL;` then drop the check constraint.
6. Verify: `\d orders` shows `status` not null and no leftover check constraint.

## Rollback and recovery
- **Schema rollback**: `ALTER TABLE orders ALTER COLUMN status DROP NOT NULL;` restores the previous definition cleanly.
- **Data recovery**: the backfill overwrites `NULL` with the sentinel; record which rows were null first if the original null-ness must be auditable.
- **Application rollback**: safe — the old application version writes nulls again, so drop the constraint before rolling back the code.

## Pre-deploy checklist
- [ ] Sentinel agreed with the finance consumer
- [ ] `SELECT count(*) FROM orders WHERE status IS NULL` returns 0
- [ ] Writers updated and deployed before the constraint
- [ ] Backfill batched, with replica lag monitored
- [ ] Constraint added `NOT VALID` and validated in a separate step