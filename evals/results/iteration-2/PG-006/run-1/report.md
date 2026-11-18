# Migration Guard Report — PG-006

**Migration:** `db/migrate/20261116100000_add_orders_status_idx.rb` (SQL: `migration.sql`)
**Statement:** `CREATE INDEX CONCURRENTLY orders_status_idx ON orders (status);`
**Verdict: ❌ NOT READY TO MERGE — HIGH severity**

---

## Blocking issue

**`CREATE INDEX CONCURRENTLY` will fail immediately because Rails wraps the migration in a transaction.**

Evidence:

- From `context.md`:
  > Rails runs each migration file inside `BEGIN … COMMIT` by default.
  > No `disable_ddl_transaction!` in the migration.
- From `migration.sql` header comment:
  > Rails wraps this migration in a transaction by default; there is no
  > `disable_ddl_transaction!` in the migration class.

PostgreSQL 15 rejects this with:

```
ERROR:  CREATE INDEX CONCURRENTLY cannot run inside a transaction block
```

Consequences at deploy time:

- `db:migrate` runs automatically in the deploy step **against the primary** (per `context.md`), so the deploy fails at migration time, not in a later verification step.
- No data is lost and the table is not corrupted — this is the HIGH (not critical) case — **but the deploy is blocked and the index the developer wanted does not exist**, so the slow query it was meant to fix stays slow.

---

## Required fix

The migration class must opt out of the DDL transaction, so the statement runs in autocommit:

```ruby
class AddOrdersStatusIdx < ActiveRecord::Migration[7.1]
  disable_ddl_transaction!

  def change
    add_index :orders, :status, name: "orders_status_idx", algorithm: :concurrently
  end
end
```

(Equivalent raw form: `disable_ddl_transaction!` + the `CONCURRENTLY` statement as written.)

Without `disable_ddl_transaction!`, the `CONCURRENTLY` keyword is both unusable and a false sense of safety — the developer's stated rationale (“I read that `CONCURRENTLY` is the safe way”) does not hold in this setup.

---

## Secondary warning — orphaned invalid index if the statement runs and fails

Even after fixing the transaction wrapper, `CREATE INDEX CONCURRENTLY` has its own failure mode: if it fails partway (lock timeout, deadlock, uniqueness violation, cancellation, connection drop), PostgreSQL **leaves behind an `INVALID` index** with the target name. Any retry with the same name will then fail with `relation "orders_status_idx" already exists`, and the invalid index adds write overhead on a write-heavy table while not being usable for reads.

Relevant facts:

- `orders` is ~9M rows and **write-heavy** (`schema.sql`, `context.md`) — an invalid index is real ongoing write cost.
- No `strong_migrations` is installed (`context.md`), so nothing will catch or clean this up automatically.

Mitigations to include with the fix:

1. After the migration, verify the index is valid:

   ```sql
   SELECT indexrelid::regclass, indisvalid
   FROM pg_index
   WHERE indexrelid = 'orders_status_idx'::regclass;
   ```

   `indisvalid` must be `true`.

2. If a previous attempt left an invalid index, clean it up **concurrently before retrying**:

   ```sql
   DROP INDEX CONCURRENTLY IF EXISTS orders_status_idx;
   ```

   (`DROP INDEX CONCURRENTLY` has the same transaction rule — it must also run outside a transaction block.)

3. Prefer a uniquely-named index per attempt if unsure, so a stale name never blocks the deploy.

---

## Checklist

| Check | Result |
|---|---|
| `CONCURRENTLY` runs outside a transaction | ❌ **Fails** — Rails default transaction wrapper; no `disable_ddl_transaction!` |
| Migration-safe on write-heavy 9M-row table (locking) | ✅ `CONCURRENTLY` is the right approach *if* it can run |
| Index name collision risk before running | ✅ No existing `orders_status_idx` (`application-usage.md`; `db/structure.sql`; no other migration touches it) |
| Failure cleanup / invalid-index handling addressed | ⚠️ Not addressed — no `strong_migrations`, no cleanup/verification step |
| Query benefit | ✅ `WHERE status = … ORDER BY placed_at DESC LIMIT 50` (controller:18) and periodic `WHERE status = 'pending'` count (job:27) will benefit from a `status` index |

---

## Summary

**Do not merge as-is.** The migration will fail on deploy because `CONCURRENTLY` is inside the Rails DDL transaction. Fix by adding `disable_ddl_transaction!` (or `disable_ddl_transaction!` + `algorithm: :concurrently` in `add_index`). Once it runs outside a transaction, it is the correct, non-blocking way to add the index on a 9M-row write-heavy `orders` table — but the PR should also document the invalid-index retry/cleanup procedure, since the team has no `strong_migrations` guard and no other index writer on `orders.status`.