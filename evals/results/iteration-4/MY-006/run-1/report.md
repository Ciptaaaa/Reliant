# Migration Guard Report

## Scope
- **Migration**: `database/migrations/2026_11_16_125000_shipments_add_status.php`
- **Engine**: MySQL 8.0.36 (InnoDB)
- **Inspected**: `migration.sql`, `schema.sql`, `application-usage.md`, `context.md`
- **Not inspected**: the analytics replica job (it runs on the replica, not the primary)
- **Unknowns**: the exact duration of the settlement transaction (2–6 minutes is stated as a range)

## Verdict
**CONDITIONAL** — the explicit `INPLACE` + `LOCK=SHARED` forces a rebuild and lets the `ALTER` wait behind a 2–6 minute transaction, piling every query on `shipments` behind it.

## Findings

### F1 — HIGH — lock-and-rewrite
- **Statement**: `ALTER TABLE shipments ADD COLUMN status VARCHAR(20) NOT NULL DEFAULT 'pending', ALGORITHM=INPLACE, LOCK=SHARED` in `migration.sql:2-3` — asks for a rebuild where an instant path exists, and must wait for the metadata lock held by the settlement transaction
- **Evidence**:
  - `application-usage.md:13` — `app/Console/Commands/SettlementReport.php:58` holds `lockForShare()` on `shipments` for 2–6 minutes every 15 minutes, on the primary
  - `migration.sql:2-3` — `ALGORITHM=INPLACE, LOCK=SHARED`, so the `ALTER` waits for that shared MDL
  - `context.md:19` — "a queued `ALTER` waiting for an MDL serialises later requests on the same table, which is the classic MDL pile-up"
- **Impact**: the `ALTER` can queue behind the settlement transaction for up to 6 minutes, and every request on `shipments` — including the highest-QPS query at `app/Http/Controllers/ShipmentTrackingController.php:19` — queues behind the waiting `ALTER`, a full table stall triggered every 15 minutes
- **Mitigation**: set `lock_wait_timeout` for the migration session so it fails fast instead of stalling, run outside the settlement window, and prefer the instant path in F2 so the MDL is held for a fraction of a second
- **Verification**: `performance_schema.metadata_locks` shows no `PENDING` MDL on `shipments` during the change, and request latency on the tracking query stays flat
- **Approval**: schedule
- **Reversible**: yes

### F2 — HIGH — lock-and-rewrite
- **Statement**: the explicit `INPLACE`/`LOCK=SHARED` forces a rebuild of 6,500,000 rows, while appending this column with a constant default supports `ALGORITHM=INSTANT` on MySQL 8.0
- **Evidence**:
  - `migration.sql:2-3` — `status` is added with a constant default `'pending'` and the developer explicitly requested `ALGORITHM=INPLACE, LOCK=SHARED`
  - `schema.sql:16` — `shipments` holds ~6,500,000 rows with ~25 inserts per second
  - `context.md:16` — on MySQL 8.0 an appended column with a constant default can use `ALGORITHM=INSTANT`, which permits only `LOCK=DEFAULT`
- **Impact**: the requested combination cannot take the instant path, so the change rebuilds 6.5M rows instead of updating the catalog, and writes block for the whole rebuild instead of a moment
- **Mitigation**: request `ALGORITHM=INSTANT, LOCK=DEFAULT` and verify with `SHOW WARNINGS` that the instant path was taken
- **Verification**: `SHOW WARNINGS` reports no algorithm escalation, and `INFORMATION_SCHEMA.INNODB_TABLES.TOTAL_ROW_VERSIONS` increments rather than the table being rebuilt
- **Approval**: schedule
- **Reversible**: yes

### F3 — INFO — no-risk-observed
- **Statement**: checked whether existing or older application code can write a row that violates the new column
- **Evidence**:
  - `application-usage.md:21` — `app/Services/Shipping/CreateShipment.php:29` inserts `status` explicitly in the new code
  - `application-usage.md:22` — older deployed code inserts without `status`, and the constant default covers it
- **Impact**: none. The column is safe for old writers because of the default, so no insert fails during the rolling deploy.
- **Mitigation**: none
- **Verification**: `SELECT COUNT(*) FROM shipments WHERE status IS NULL` returns 0 after the deploy
- **Approval**: none
- **Reversible**: yes

## Safer migration path
1. Take the instant path and let the server report what it did:
   ```sql
   SET SESSION lock_wait_timeout = 5;
   ALTER TABLE shipments ADD COLUMN status VARCHAR(20) NOT NULL DEFAULT 'pending', ALGORITHM=INSTANT, LOCK=DEFAULT;
   SHOW WARNINGS;
   ```
2. If `INSTANT` is rejected on the deployed patch version, fall back to `INPLACE` with `LOCK=DEFAULT` **outside** the settlement window, with `lock_wait_timeout` set.
3. Verify: `SHOW COLUMNS FROM shipments LIKE 'status'` shows the default, and no `PENDING` MDL appears during the change.

## Rollback and recovery
- **Schema rollback**: `ALTER TABLE shipments DROP COLUMN status, ALGORITHM=INSTANT;` is instant on 8.0.29+; on earlier 8.0 patch versions a drop is an INPLACE rebuild, so check the patch version first.
- **Data recovery**: no existing value is changed; the default only applies to new rows, so no recovery is needed.
- **Application rollback**: safe — the old application version inserts without `status` and the default fills it.

## Pre-deploy checklist
- [ ] `SHOW WARNINGS` confirms the instant path, or the rebuild runs in a window
- [ ] `lock_wait_timeout` set for the migration session
- [ ] Change scheduled outside the 15-minute settlement window
- [ ] `performance_schema.metadata_locks` watched during the change