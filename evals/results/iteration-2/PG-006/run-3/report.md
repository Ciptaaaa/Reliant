# Migration Guard Report — PG-006

## Verdict: ❌ BLOCK MERGE (HIGH)

The migration will fail at deploy time. `CREATE INDEX CONCURRENTLY` cannot execute inside the implicit `BEGIN … COMMIT` that Rails wraps around every migration by default.

## Findings

### 1. `CREATE INDEX CONCURRENTLY` runs inside a transaction — blocking

Evidence from `migration.sql`:
```sql
-- Rails wraps this migration in a transaction by default; there is no
-- `disable_ddl_transaction!` in the migration class.

CREATE INDEX CONCURRENTLY orders_status_idx ON orders (status);
```

Corroborating evidence from `context.md`:
> Rails runs each migration file inside `BEGIN … COMMIT` by default.
> No `disable_ddl_transaction!` in the migration.

PostgreSQL 15 rejects this outright:
> `CREATE INDEX CONCURRENTLY cannot run inside a transaction block`

Combined with the deployment fact that `db:migrate` runs automatically in the deploy step, **this migration takes the deploy down**. Every subsequent attempt re-hits the same error until the migration file is fixed.

### 2. Residual invalid index risk (second-order, applies to the fix)

The current form never reaches PostgreSQL's index build, so no invalid index is created *today*. But the obvious fix (adding `disable_ddl_transaction!` or `self.disable_ddl_transaction = true`) introduces a new failure mode: if a non-transactional `CREATE INDEX CONCURRENTLY` fails partway (cancellation, disk pressure, connection drop on 9M rows), PostgreSQL leaves behind an **`INVALID`** index named `orders_status_idx`. On the next migration run, `CREATE INDEX CONCURRENTLY` will then error with:

> `relation "orders_status_idx" already exists`

leaving the deploy wedged until someone manually runs `DROP INDEX CONCURRENTLY orders_status_idx;` on the primary. The fix must therefore be paired with a runbook step or pre-flight `DROP INDEX IF EXISTS … CONCURRENTLY` cleanup.

### 3. Instrumentation confirms this is the intended new index

Evidence from `application-usage.md`:
> - No other migration creates or removes an index on `orders.status`.
> - `db/structure.sql` has no index named `orders_status_idx`.

Evidence from `schema.sql`:
> `-- No index on status. Rows: ~9,000,000.`

So the name `orders_status_idx` is unique, no competing index writer exists, and the index really is absent — the migration's intent is correct, only its execution form is wrong.

### 4. Reads and writes are consistent with a status index

- Read 1 (`orders_controller.rb:18`): `where(status: …).order(placed_at: :desc).limit(50)` — a single-column `(status)` index helps the filter but not the sort. Not a blocker; the goal of the migration is accepted.
- Read 2 (`status_report_job.rb:27`): `where(status: 'pending').count` — benefits directly.
- Writes (`order.rb:44`, `orders_controller.rb:61`) update/insert `status` — non-blocking with `CONCURRENTLY`, which is exactly why the developer chose it.

### 5. `strong_migrations` is absent

Evidence from `context.md`:
> `strong_migrations` **not** installed

This is the enabler of the bug: with `strong_migrations` installed, Rails would have raised at migration-authoring time. Its absence means every future migration in this repo is one careless line away from the same class of failure; worth a follow-up, but out of scope for this PR.

### 6. Replica caveat is a non-issue here

Evidence from `application-usage.md`:
> Replica read paths (the primary handles all traffic in this deployment).

So no replica-lag concern from the online index build; `CONCURRENTLY` is still correct, and no separate replica-side DDL is needed.

## Required changes before merge

1. **Add `disable_ddl_transaction!` to the migration class** so Rails does not wrap it in `BEGIN … COMMIT`. (The `-- SQL form shown` comment hides the Ruby; the change belongs in `db/migrate/20261116100000_add_orders_status_idx.rb`.)
2. **Add a pre-flight `DROP INDEX CONCURRENTLY IF EXISTS orders_status_idx;`** in the same migration (or a documented runbook step) so an `INVALID` leftover from a prior failed run does not wedge the deploy.
3. **Runbook note** for the operator: if the migration fails mid-build on the 9M-row table, manually run `DROP INDEX CONCURRENTLY orders_status_idx;` on the primary before retrying. Verify success with `SELECT indislive, indisvalid FROM pg_index WHERE indexrelid = 'orders_status_idx'::regclass;`.
4. **Optional, non-blocking**: install `strong_migrations` so this class of migration is caught at authoring time, not deploy time.

## Summary

| Check | Result |
|---|---|
| Executes under Rails transactional wrapper | ❌ Fails: `CONCURRENTLY cannot run inside a transaction block` |
| Handles residual `INVALID` index from prior failure | ❌ Not addressed |
| Index name conflicts | ✅ None |
| Intended index absent (confirmed by `structure.sql`) | ✅ |
| Matches read/write usage | ✅ |
| Replica implications | ✅ (none; primary-only) |

**Verdict: BLOCK.** Deploy fails on `db:migrate`. Fix `disable_ddl_transaction!` + `DROP INDEX IF EXISTS … CONCURRENTLY` pre-flight, then re-review. The design intent is correct; only the execution wrapper is wrong.