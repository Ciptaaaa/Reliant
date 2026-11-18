# Migration Guard Report — PG-006

## Verdict

**HIGH — BLOCK: do not merge.** As written, this migration **will fail at deploy time**. It is not a performance problem and causes no data loss, but `db:migrate` in the deploy step will abort.

## Evidence

### 1. The statement is wrapped in a transaction, which PostgreSQL forbids for `CONCURRENTLY`

`migration.sql`:
> `CREATE INDEX CONCURRENTLY orders_status_idx ON orders (status);`

The header of the same file:
> `-- Rails wraps this migration in a transaction by default; there is no`
> `-- disable_ddl_transaction! in the migration class.`

`context.md` / deployment facts:
> "Rails runs each migration file inside `BEGIN … COMMIT` by default."
> "`db:migrate` runs automatically in the deploy step against the primary."
> "No `disable_ddl_transaction!` in the migration."

PostgreSQL 15 raises `CREATE INDEX CONCURRENTLY cannot run inside a transaction block`. The migration will therefore fail, the transaction will roll back, and the deploy step will stop.

### 2. No later migration could be masking this

`application-usage.md`, Index writers:
> "No other migration creates or removes an index on `orders.status`."
> "`db/structure.sql` has no index named `orders_status_idx`."

This confirms the index does not already exist and no parallel change interferes.

## Required fix

Tell Rails not to wrap this migration in a transaction. In the migration class:

```ruby
class AddOrdersStatusIdx < ActiveRecord::Migration[7.1]
  disable_ddl_transaction!

  def change
    add_index :orders, :status, name: "orders_status_idx", algorithm: :concurrently
  end
end
```

As SQL, this runs as a standalone:
```sql
CREATE INDEX CONCURRENTLY orders_status_idx ON orders (status);
```

Once the migration runs outside a transaction, a failure leaves an **INVALID** index behind. PostgreSQL does not drop it automatically. Any future attempt to create `orders_status_idx` will fail with "already exists". Before re-running, check and clean up:

```sql
SELECT indexrelid::regclass, indrelid::regclass, indisvalid
FROM pg_index
WHERE NOT indisvalid
  AND indrelid = 'orders'::regclass;
-- If present:
DROP INDEX CONCURRENTLY IF EXISTS orders_status_idx;
```

## Secondary observations (non-blocking, for the PR author)

- **The index on `status` alone is of limited use for the slow query.** The controller query (`orders_controller.rb:18`) filters on `status` and orders by `placed_at DESC` with `LIMIT 50`. A plain `(status)` index still forces a sort. On a write-heavy 9M-row table, consider `(status, placed_at DESC)` so the `ORDER BY … LIMIT 50` is satisfied by an index scan. The job query (`status_report_job.rb:27`, `count` on `'pending'`) is served by either.
- **Low `status` selectivity.** With only a handful of distinct statuses, the index may not be chosen for a broad filter. That's fine for the `pending` count and the `LIMIT`-ordered list, but check `EXPLAIN` on production-shaped data.
- **Locking.** Outside a transaction, `CONCURRENTLY` takes only a `SHARE UPDATE EXCLUSIVE` lock. This is safe for a write-heavy `orders` table, unlike the plain `CREATE INDEX` that the transaction wrapper would have silently forced had `CONCURRENTLY` not errored.
- **Install `strong_migrations`.** It catches exactly this pattern (and `add_index … algorithm: :concurrently` without `disable_ddl_transaction!`) at CI time.

**Verdict:** Blocked. Add `disable_ddl_transaction!` and use `algorithm: :concurrently`, then re-review.