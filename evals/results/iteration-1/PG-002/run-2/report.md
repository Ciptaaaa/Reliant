# Migration Guard report — PG-002

## Verdict

**BLOCK — do not merge as written.** The migration will fail on deploy and, even if the data were clean, it is written in the one form that is unsafe for a live table of this size.

## Findings

### 1. The migration will error out: NULL rows exist

The developer's claim that "the column already has values for all new orders" does not mean the column has no NULLs. The schema notes record otherwise:

> ```
> --   SELECT count(*) FROM orders;                         -> 4,812,004
> --   SELECT count(*) FROM orders WHERE status IS NULL;    -> 36,117
> ```

PostgreSQL refuses `SET NOT NULL` while any NULL remains, so the deploy step will abort (inside its transaction) and the release will fail. There are **36,117 rows** to deal with, not zero.

### 2. The NULLs are still being produced — a backfill alone would not hold

NULL statuses are not only legacy data. Current code inserts them:

> `src/domain/orders/create.ts:18` — `await prisma.order.create({ data: { customerId, status: null, totalCents } });`
> "**new orders are inserted with `status = null`** until the fulfilment worker sets it."

> `src/imports/legacy-shop.ts:12` — legacy importer, inserts rows without `status`.

> Finance reporting … "is expected to keep producing rows with `NULL` status until the importer is retired."

So a one-time `UPDATE ... SET status = ...` would be undone by the next 400 inserts/minute, and finance depends on NULL meaning "unknown". Making the column `NOT NULL` requires a product/data decision (a sentinel status such as `'pending'`) and coordinated code/schema changes — this is not a drop-in constraint change.

### 3. The statement's locking is unsafe, whatever the data says

`ALTER TABLE orders ALTER COLUMN status SET NOT NULL` takes `ACCESS EXCLUSIVE`, which blocks reads and writes for the duration of the operation. In PG 12+ it avoids a full table rewrite, but it must **scan the whole table** to prove there are no NULLs, unless a valid `CHECK` constraint already proves it. On 4.8M rows that scan holds `ACCESS EXCLUSIVE` for an extended period while the deploy runs with **no maintenance window**, and the table gets ~400 inserts/min at peak. Blocking writers that long will back up the API and the fulfilment worker queue.

The `orders_status_idx` on `status` does not help: index entries for NULLs are not a substitute for the table scan proof, and the planner does not use it to validate `SET NOT NULL`.

## Recommended sequence (instead of `migration.sql`)

```sql
-- Step 1 (separate migration/deploy; brief lock only):
ALTER TABLE orders
  ADD CONSTRAINT orders_status_not_null_check
  CHECK (status IS NOT NULL) NOT VALID;

-- Step 2 (no long exclusive lock; only blocks writers touching changed rows briefly):
ALTER TABLE orders VALIDATE CONSTRAINT orders_status_not_null_check;
```

Then, in a later migration (Step 3, after Step 2 has been applied):

```sql
ALTER TABLE orders ALTER COLUMN status SET NOT NULL;
-- optional cleanup:
ALTER TABLE orders DROP CONSTRAINT orders_status_not_null_check;
```

Once the validated `CHECK` exists, the final `SET NOT NULL` is fast because PostgreSQL can rely on the constraint instead of rescanning the table.

**This sequence still does not work for this table** until the 36,117 NULLs are resolved and the writers (`create.ts`, `legacy-shop.ts`, and any lagging fulfilment worker) stop inserting NULL. It also conflicts with finance's stated expectation that NULL means "unknown"; that contract needs to be renegotiated (or a sentinel used) before NOT NULL can be adopted. Also check the fulfilment worker's deployment version — the context says it "may lag behind the API" — so a sentinel-value change must be backward compatible with the older worker.

## Residual risk / notes

- **Lagging worker**: even after backfill and code changes, an older fulfilment worker could still write the old shape; confirm its deployed version before enforcing.
- **Lock timeout**: the deploy runs migrations inside a single transaction with no maintenance window. Add a `lock_timeout` (e.g. `SET lock_timeout = '5s'`) to both ALTER migrations so a blocked lock fails fast rather than stalling the deploy and the table.
- **Volume**: 4.8M rows; `VALIDATE CONSTRAINT` scans the whole table but takes only `SHARE UPDATE EXCLUSIVE`, so reads and writes continue.

**Summary:** blocked because (a) the referenced rows would always fail the migration — 36,117 NULL rows are still present, (b) NULLs are still being inserted now, so a backfill will not hold, and (c) `SET NOT NULL` alone takes `ACCESS EXCLUSIVE` and scans the whole table. Use `CHECK … NOT VALID` → `VALIDATE` → `SET NOT NULL`, after a data/product decision on NULL semantics and code changes stop the NULL writes.