# Migration Guard Report — PG-006

## Verdict

**BLOCK — do not merge.** The migration as written will fail on deploy. It is `HIGH` severity: no data loss, but the `db:migrate` deploy step will abort.

## Blocking issue: `CREATE INDEX CONCURRENTLY` inside a transaction

`migration.sql` contains:

```sql
CREATE INDEX CONCURRENTLY orders_status_idx ON orders (status);
```

The migration carries this note:

> Rails wraps this migration in a transaction by default; there is no
> `disable_ddl_transaction!` in the migration class.

`context.md` confirms the same:

> **Application**: Rails 7.1 … the team uses default Rails DDL transaction behaviour
> Rails runs each migration file inside `BEGIN … COMMIT` by default.
> No `disable_ddl_transaction!` in the migration.

PostgreSQL 15 rejects `CREATE INDEX CONCURRENTLY` inside a transaction block with:

```
CREATE INDEX CONCURRENTLY cannot run inside a transaction block
```

Because `db:migrate` runs automatically in the deploy step against the primary, the migration fails and the deploy stops. This is the primary reason to block.

## Required fix

The migration must opt out of the implicit transaction. In the Rails migration:

```ruby
class AddOrdersStatusIdx < ActiveRecord::Migration[7.1]
  disable_ddl_transaction!

  def change
    add_index :orders, :status, name: 'orders_status_idx', algorithm: :concurrently
  end
end
```

Or, as raw SQL:

```ruby
class AddOrdersStatusIdx < ActiveRecord::Migration[7.1]
  disable_ddl_transaction!

  def up
    execute 'CREATE INDEX CONCURRENTLY orders_status_idx ON orders (status)'
  end

  def down
    execute 'DROP INDEX CONCURRENTLY IF EXISTS orders_status_idx'
  end
end
```

With `disable_ddl_transaction!`, the statement runs outside a transaction and `CONCURRENTLY` is accepted.

## Secondary warning: invalid index left behind after a failed build

`CONCURRENTLY` runs outside a transaction once fixed, so it is **not atomic**. If the build fails partway (lock timeout, cancelled query, connection drop, unique violation — not applicable here since the index isn't unique), PostgreSQL leaves an **invalid** index named `orders_status_idx`. This matters for your team because:

- The invalid index is not used by the planner for reads, so the slow query at `orders_controller.rb:18` stays slow.
- A retry that issues plain `CREATE INDEX CONCURRENTLY orders_status_idx …` will fail with `relation "orders_status_idx" already exists`.

**Mitigation:** after any failed `CONCURRENTLY` build, run `DROP INDEX CONCURRENTLY IF EXISTS orders_status_idx;` before retrying, and verify with:

```sql
SELECT indexrelid::regclass, indisvalid
FROM pg_index
WHERE indexrelid = 'orders_status_idx'::regclass;
```

Idempotent `up` (or a pre-check that drops an invalid index of the same name) is recommended for a deploy-step migration.

## Collateral checks (no blockers found)

I checked the surrounding usage to make sure the index itself is appropriate, not just the transaction issue:

- **Write cost vs. benefit.** `context.md` describes the table as "~9M rows, write-heavy". `application-usage.md` lists a read on `status` (controller `:18`, `ORDER BY placed_at DESC LIMIT 50`) and an every-5-minutes `count` on `status = 'pending'` (`status_report_job.rb:27`). The index is a net win for those reads; the added write cost is one index maintenance per `INSERT` (`orders_controller.rb:61`) and per fulfilment `UPDATE` (`order.rb:44`). `CONCURRENTLY` avoids a long `ACCESS EXCLUSIVE`/`SHARE` lock on the 9M-row write-heavy table during the build. Good choice.
- **Index-name collision.** `application-usage.md` states:
  > No other migration creates or removes an index on `orders.status`.
  > `db/structure.sql` has no index named `orders_status_idx`.

  and `schema.sql` confirms:
  > `-- No index on status.`

  So the name is free and the index does not already exist on the primary. No collision.
- **Query shape.** The index on `(status)` supports the `WHERE status = ?` predicate. The `ORDER BY placed_at DESC` with `LIMIT 50` will sort; a composite `(status, placed_at DESC)` could serve both predicate and ordering and return from the index without a sort. **Non-blocking optimization suggestion** — measure before adding, since it changes index size and write cost. Also note that if `status` has few distinct values (the example uses `'pending'`), a plain btree may not be very selective; the `count` job will always do an index-only scan only if the visibility map allows it, otherwise a full heap. Verify on a replica or with `EXPLAIN` that the planner actually chooses the index for your workloads before relying on it to fix the page.
- **Replica reads.** `application-usage.md` states:
  > Replica read paths (the primary handles all traffic in this deployment).

  Since primary handles all traffic, `CONCURRENTLY`'s benefit (no long lock on the primary during build) is fully realized, and no replica-specific issue applies.
- **Schema source of truth.** The team keeps `db/structure.sql` (per `application-usage.md`). Subsequent `db:migrate` runs will update it; just ensure the migration version is recorded after a successful run and invalid state is cleaned before that.

## Summary

| Item | Status |
|---|---|
| `CREATE INDEX CONCURRENTLY` in a default transactional migration | **BLOCKER** — fails with `cannot run inside a transaction block` |
| Invalid-index left-behind risk if built outside a transaction and fails | **Warn** — add idempotent cleanup / pre-check |
| Index name free, no other migration touches it | OK |
| Index appropriate for the read pattern | OK; consider `(status, placed_at DESC)` as optional follow-up |

**Required action before merge:** add `disable_ddl_transaction!` to the migration and use `add_index … algorithm: :concurrently` (or `execute` outside a transaction), plus cleanup for any pre-existing invalid index. Once fixed, the deploy should succeed.