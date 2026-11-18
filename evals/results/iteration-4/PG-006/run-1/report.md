# Migration Guard Report

## Scope
- **Migration**: `db/migrate/20261116100000_add_orders_status_idx.rb` (SQL form in `migration.sql`)
- **Engine**: PostgreSQL 15
- **Inspected**: `migration.sql`, `schema.sql`, `application-usage.md`, `context.md`
- **Not inspected**: replica read paths (the primary handles all traffic per `application-usage.md`)
- **Unknowns**: none material; the transaction behaviour is stated in `context.md`

## Verdict
**CONDITIONAL** — `CREATE INDEX CONCURRENTLY` cannot run inside the transaction Rails wraps around the migration, so the deploy fails.

## Findings

### F1 — HIGH — index-build
- **Statement**: `CREATE INDEX CONCURRENTLY orders_status_idx ON orders (status)` in `migration.sql:7` — builds the index concurrently, which PostgreSQL refuses inside a transaction block
- **Evidence**:
  - `migration.sql:7` — `CREATE INDEX CONCURRENTLY`, and the comment at `migration.sql:3-4` states there is no `disable_ddl_transaction!`
  - `context.md:14` — Rails runs each migration file inside `BEGIN … COMMIT` by default
  - `context.md:15` — `db:migrate` runs automatically in the deploy step
- **Impact**: PostgreSQL rejects the statement with `CREATE INDEX CONCURRENTLY cannot run inside a transaction block`, so the deploy step fails and every retry fails the same way until the migration file is fixed; no data is lost
- **Mitigation**: mark the migration non-transactional with `disable_ddl_transaction!`, or run the statement outside the wrapping transaction
- **Verification**: the migration applies and `SELECT indexname FROM pg_indexes WHERE indexname = 'orders_status_idx'` returns one row
- **Approval**: none
- **Reversible**: yes

### F2 — HIGH — index-build
- **Statement**: a concurrent build that fails partway leaves an `INVALID` index behind, which wedges the retry
- **Evidence**:
  - `migration.sql:7` — the statement is `CONCURRENTLY`, which is not atomic even once the transaction problem is fixed
  - `application-usage.md:31` — no other migration creates `orders_status_idx`, and `db/structure.sql` has none, so the name is free today and will collide after a failed build
  - `schema.sql:15` — `orders` has ~9,000,000 rows, so a build takes long enough to be interrupted
- **Impact**: if the build is cancelled or the connection drops, PostgreSQL leaves `orders_status_idx` as `INVALID`; the next run fails with `relation "orders_status_idx" already exists`, wedging the deploy until someone drops it manually
- **Mitigation**: pair the non-transactional migration with a runbook step `DROP INDEX CONCURRENTLY IF EXISTS orders_status_idx;` before building, and run it in a quiet period
- **Verification**: `SELECT indisvalid FROM pg_index WHERE indexrelid = 'orders_status_idx'::regclass` returns true, not false
- **Approval**: none
- **Reversible**: yes

## Safer migration path
1. Make the migration non-transactional:
   ```ruby
   class AddOrdersStatusIdx < ActiveRecord::Migration[7.1]
     disable_ddl_transaction!
     def change
       add_index :orders, :status, name: "orders_status_idx", algorithm: :concurrently
     end
   end
   ```
2. Clear any leftover invalid index: `DROP INDEX CONCURRENTLY IF EXISTS orders_status_idx;`
3. Run `db:migrate` outside the deploy transaction so the build is not wrapped.
4. Verify `indisvalid` is true and the query at `app/controllers/orders_controller.rb:18` uses the index.

## Rollback and recovery
- **Schema rollback**: `DROP INDEX CONCURRENTLY orders_status_idx;` — non-blocking and clean.
- **Data recovery**: not applicable; an index build changes no row data.
- **Application rollback**: safe — no application code depends on the index; it only changes query plans.

## Pre-deploy checklist
- [ ] `disable_ddl_transaction!` present in the migration
- [ ] No leftover `INVALID` index with the same name
- [ ] `db:migrate` is not wrapped in the deploy transaction
- [ ] Build scheduled outside peak traffic, with connection-drop handling in the runbook