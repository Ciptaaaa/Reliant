# Migration Guard Report

## Scope
- **Migration**: `database/migrations/2026_11_16_131500_customers_rename_mobile.php`
- **Engine**: MySQL 8.0.36 (InnoDB)
- **Inspected**: `migration.sql`, `schema.sql`, `application-usage.md`, `context.md`
- **Not inspected**: mobile app clients that receive `mobile` in the JSON response (an API contract change)
- **Unknowns**: how long the ECS rolling update takes; several minutes is stated in `context.md`

## Verdict
**CONDITIONAL** — the rename is instant for the engine, but old tasks and the nightly cron keep reading `mobile` during and after the rolling update.

## Findings

### F1 — HIGH — rename
- **Statement**: `ALTER TABLE customers RENAME COLUMN mobile TO mobile_phone, ALGORITHM=INSTANT` in `migration.sql:2` — renames the column immediately, before the new tasks start
- **Evidence**:
  - `application-usage.md:5` — `app/Http/Controllers/CustomerController.php:52` returns `only([... 'mobile'])` in the deployed code
  - `application-usage.md:10` — `app/Services/Sms/SendVerification.php:31` sends to `$customer->mobile`, so SMS verification uses the old name
  - `application-usage.md:23` — `app/Console/Commands/ExportCustomers.php:26` selects `mobile` from the last released image, which keeps running until a new image ships
  - `context.md:16` — the migration runs before the new tasks start, and old tasks keep serving for several minutes
- **Impact**: from the moment the rename commits, old tasks raise "Unknown column 'mobile'" on the customer endpoint and on SMS verification, and the nightly export fails until a new scheduler image is released; no data is lost because the values move with the rename
- **Mitigation**: use expand-and-contract instead of a direct rename — add `mobile_phone`, deploy dual write/read, backfill, switch reads, then drop `mobile`; a direct rename is safe only with an explicitly scheduled stop-the-world deploy
- **Verification**: `grep -rn "mobile\b" app/` in the deployed image returns only `mobile_phone`, and the scheduler spec is updated before the rename runs
- **Approval**: schedule
- **Reversible**: yes

### F2 — LOW — lock-and-rewrite
- **Statement**: `RENAME COLUMN` takes a metadata lock on `customers` but completes immediately and keeps `customers_mobile_idx` attached
- **Evidence**:
  - `migration.sql:2` — `RENAME COLUMN` with `ALGORITHM=INSTANT`
  - `application-usage.md:29` — "`RENAME COLUMN` keeps the index attached (index name is unchanged)"
  - `schema.sql:13` — `KEY customers_mobile_idx (mobile)`, ~620,000 rows
- **Impact**: momentary metadata lock only; the risk in this migration is deploy order, not lock duration
- **Mitigation**: none for the lock; keep the rename out of a period with long-running transactions so the momentary MDL does not queue
- **Verification**: `SHOW INDEX FROM customers` still lists `customers_mobile_idx`, and no `PENDING` MDL appears during the change
- **Approval**: none
- **Reversible**: yes

## Safer migration path
1. Add the new column online: `ALTER TABLE customers ADD COLUMN mobile_phone VARCHAR(32) DEFAULT NULL, ALGORITHM=INSTANT, LOCK=DEFAULT;`
2. Deploy code that writes both `mobile` and `mobile_phone` and still reads `mobile`.
3. Backfill in batches with an idempotent predicate:
   `UPDATE customers SET mobile_phone = mobile WHERE mobile_phone IS NULL AND mobile IS NOT NULL LIMIT 5000;` repeated until 0 rows are affected.
4. Deploy code that reads `mobile_phone` only, and update the scheduler image and spec.
5. After one full deploy cycle, drop the old column: `ALTER TABLE customers DROP COLUMN mobile;` (instant on 8.0.29+).
6. Verify: the customer endpoint returns `mobile_phone`, SMS verification sends, and the nightly export completes.

## Rollback and recovery
- **Schema rollback**: `ALTER TABLE customers RENAME COLUMN mobile_phone TO mobile;` restores the old name instantly and keeps every value.
- **Data recovery**: a rename moves no data, so nothing is lost; the recovery problem is code, not data.
- **Application rollback**: rolling the application back after a direct rename fails, because the old image reads `mobile`; roll the schema back first.

## Pre-deploy checklist
- [ ] Deploy order decided: expand-and-contract, or a documented stop-the-world window
- [ ] Scheduler image and spec updated to `mobile_phone`
- [ ] Old ECS tasks fully drained before any direct rename
- [ ] Mobile app clients notified of the JSON key change