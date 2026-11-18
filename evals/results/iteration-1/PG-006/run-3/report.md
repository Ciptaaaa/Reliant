# Migration Guard Report — PG-006

**Migration:** `db/migrate/20261116100000_add_orders_status_idx.rb` (SQL form)
**Statement:** `CREATE INDEX CONCURRENTLY orders_status_idx ON orders (status);`

## Verdict: ❌ NOT READY TO SHIP (HIGH)

This migration **will fail on deploy**. It is not a data-loss event, but the deploy step fails and can leave the schema in an inconsistent state.

## Findings

### 1. `CREATE INDEX CONCURRENTLY` inside a transaction — deploy breaker (HIGH)

Evidence from `context.md`:

> - Rails runs each migration file inside `BEGIN … COMMIT` by default.
> - `db:migrate` runs automatically in the deploy step against the primary.
> - No `disable_ddl_transaction!` in the migration.

Evidence from `migration.sql`:

> ```
> -- Rails wraps this migration in a transaction by default; there is no
> -- `disable_ddl_transaction!` in the migration class.
> ```

PostgreSQL 15 refuses this statement inside a transaction block:

> `CREATE INDEX CONCURRENTLY cannot run inside a transaction block`

Because `db:migrate` runs automatically in the deploy step, the deploy job will fail at this migration. Every later migration in the run is blocked as well.

### 2. Invalid index left behind on a non-transactional failure (HIGH, conditional)

If the transaction wrapper were removed and the `CONCURRENTLY` build failed partway (lock timeout, deadlock, cancellation, disk/connection drop), PostgreSQL would still leave behind a **`status = 'invalid'`** version of `orders_status_idx`. That leftover index:
- keeps consuming write overhead on a write-heavy table, and
- silently doesn't serve the read query.

This one isn't the direct blocker here (the transaction blocks the statement before it ever starts), but it becomes the next failure mode as soon as the transaction issue is fixed. The migration should be written so a failed run leaves a clean state and is safe to re-run.

### 3. Schema/intent fit — reads confirmed, but verify

Evidence from `application-usage.md`:

> - `app/controllers/orders_controller.rb:18` — `Order.where(status: params[:status]).order(placed_at: :desc).limit(50)` → the slow query this index is meant to fix.
> - `app/jobs/status_report_job.rb:27` — `Order.where(status: 'pending').count` every 5 minutes.

- `status` is only selective if the column has many distinct values. `varchar(24)` with no value list given — if `status` has only a handful of values (e.g. `pending/paid/shipped/…`), a plain `(status)` btree on 9M rows helps the `count` for `status='pending'`, but the list query's `ORDER BY placed_at DESC LIMIT 50` may still sort. If the list query is the stated goal, `(status, placed_at DESC)` is likely a better match. Confirm the actual cardinality of `status` before merging — this doesn't block, but it may not fix the reported slowness.

Evidence that nothing else touches this index (no concurrency conflict):

> - No other migration creates or removes an index on `orders.status`.
> - `db/structure.sql` has no index named `orders_status_idx`.

## Required fix

In the migration class:

```ruby
class AddOrdersStatusIdx < ActiveRecord::Migration[7.1]
  disable_ddl_transaction!   # required: CIC cannot run in a transaction

  def change
    add_index :orders, :status, name: 'orders_status_idx', algorithm: :concurrently
  end
end
```

And make it re-runnable / self-healing, e.g. guard with `if_not_exists: true` (Rails 7.1 supports this on `add_index`) so a previously-failed run leaves an invalid index that isn't mistaken for a completed one — or schedule a follow-up to `DROP INDEX CONCURRENTLY IF EXISTS orders_status_idx` before re-running. Verify after deploy: `SELECT indexrelid::regclass, indisvalid FROM pg_index WHERE indrelid = 'orders'::regclass;`.

## Summary

| Check | Result |
|---|---|
| Runs inside transaction? | **Yes — CIC forbidden → deploy fails** |
| Leaves invalid index on failure? | Yes, once transaction issue is fixed — must handle re-run |
| Index name conflicts | None found |
| Matches slow query | Partial — confirm cardinality; consider `(status, placed_at DESC)` |

**Verdict: do not merge as-is.** Add `disable_ddl_transaction!` and use `add_index …, algorithm: :concurrently` (plus re-runnability handling), and confirm the index columns against the actual list query.