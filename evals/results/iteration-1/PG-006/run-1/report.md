# Migration Guard Report — PG-006

## Summary
**Verdict: 🔴 FAIL — Do not merge as written.**

The migration will fail at deploy time. `CREATE INDEX CONCURRENTLY` is wrapped in a Rails-managed transaction that it cannot run inside. No data loss, but the deploy breaks and leaves no index in place.

## Evidence

**1. The migration is a `CONCURRENTLY` statement:**
> `CREATE INDEX CONCURRENTLY orders_status_idx ON orders (status);`

**2. Rails wraps it in a transaction, and the migration does not opt out:**
> "Rails wraps this migration in a transaction by default; there is no `disable_ddl_transaction!` in the migration class."

> "`db:migrate` runs automatically in the deploy step against the primary."
> "No `disable_ddl_transaction!` in the migration."

**3. PostgreSQL 15 rejects this combination:**
> "`CREATE INDEX CONCURRENTLY` cannot run inside a transaction block: PostgreSQL rejects it with
> `CREATE INDEX CONCURRENTLY cannot run inside a transaction block`."

**4. Confirming no index currently exists (so nothing is silently masked):**
> "`db/structure.sql` has no index named `orders_status_idx`."
> "No other migration creates or removes an index on `orders.status`."

**5. The table this runs against is large and write-heavy:**
> "`orders`, ~9M rows, write-heavy." / "Rows: ~9,000,000."

## What happens on deploy
`db:migrate` runs `BEGIN … CREATE INDEX CONCURRENTLY …`. On PostgreSQL 15 this raises:

```
ERROR: CREATE INDEX CONCURRENTLY cannot run inside a transaction block
```

The transaction aborts and the deploy step fails. No index is created.

## Secondary hazard — invalid index on retry (important)
Suppose the developer "fixes" this by adding `disable_ddl_transaction!` and running it outside a transaction. Then if the concurrent build is interrupted (deploy kill, timeout, lock wait, connection drop), PostgreSQL leaves behind a **`pg_index.indisvalid = false`** stub named `orders_status_idx`. Consequences:

- The stub is **not usable** by the planner, so the slow query at `orders_controller.rb:18` is not fixed.
- A retry of the identical `CREATE INDEX CONCURRENTLY orders_status_idx …` will fail with:
  `ERROR: relation "orders_status_idx" already exists`.
- Cleanup requires an explicit `DROP INDEX CONCURRENTLY IF EXISTS orders_status_idx;` before retrying.

The current migration has no idempotent guard against this.

## Related observations (not blocking, but note)
- **Write amplification during build:** even a correct concurrent build on a 9M-row write-heavy table cleans up dead tuples from `UPDATE`s to `status` (`app/models/order.rb:44`) and inserts (`orders_controller.rb:61`). Expect a long-running build, monitoring `pg_stat_progress_create_index`, and a `lock_timeout`/`statement_timeout` policy.
- **Low card value check:** the column is `varchar(24)` and the query is an equality filter. Confirm `status` has enough distinct values (and that `orders_controller.rb:18` really benefits) before paying the write cost. The 5-minute `status_report_job.rb:27` `count` may also change its plan — verify it doesn't regress.
- **Replica paths "not searched"** — the file says the primary handles all traffic, so no further path analysis is warranted here, but that assumption is load-bearing; confirm it before shipping.

## Required changes before merge
1. **Add `disable_ddl_transaction!`** to the migration class (equivalent to `algorithms: :concurrently` in strong_migrations).
2. **Make the migration idempotent** — wrap the build, or pre-emptively run:
   ```sql
   DROP INDEX CONCURRENTLY IF EXISTS orders_status_idx;
   ```
   before the `CREATE INDEX CONCURRENTLY` (or detect invalidity and drop-and-recreate).
3. **Consider strong_migrations**, which would have caught this automatically.
4. **Gate the deploy**: do not run this inline in the standard `db:migrate` path alongside other migrations without verifying the timeout budget. `CREATE INDEX CONCURRENTLY` on 9M rows may exceed default statement/lock timeouts.

## Bottom line
- **Current file:** 🔴 fails — `CREATE INDEX CONCURRENTLY cannot run inside a transaction block`.
- **Fix:** opt out of the DDL transaction (`disable_ddl_transaction!`), add an idempotent `DROP INDEX CONCURRENTLY IF EXISTS` guard for the invalid-index case, and validate the build against the write-heavy table with a timeout budget before shipping.