# Migration Guard Report — PG-002: `orders_status_not_null`

## Verdict

**BLOCK.** `ALTER TABLE orders ALTER COLUMN status SET NOT NULL;` will fail on deploy (NULL rows exist) and, if it didn't, would take `ACCESS EXCLUSIVE` and scan the whole 4.8M-row table, blocking all reads and writes for the duration. Replace it with the `CHECK … NOT VALID` → `VALIDATE` → `SET NOT NULL` sequence (after resolving the NULL rows), and account for the rolling deploy and lagging consumers.

---

## Evidence

### 1. The premise ("it already has values") is false — NULLs exist today

From `schema.sql`:
```
SELECT count(*) FROM orders WHERE status IS NULL;    -> 36,117
-- The NULL rows are historical imports from the legacy shop system (before 2023-06).
```

PostgreSQL's `SET NOT NULL` verifies every row. With 36,117 NULLs present at migration time, the statement raises `ERROR: column "status" of relation "orders" contains null values`. The deploy transaction rolls back — the migration does not "just work."

The developer's claim is contradicted by the application's own behaviour anyway. From `application-usage.md`:
> `src/domain/orders/create.ts:18` … **new orders are inserted with `status = null`** until the fulfilment worker sets it.

So even after backfilling historical rows, the API inserts NULLs on every new order until the worker updates them. A NOT NULL constraint applied *before* the write path stops producing NULLs will cause **runtime write failures** in production, not just a migration failure. There is a window between `INSERT` (status NULL) and the fulfilment worker's `UPDATE` in which the row is non-conforming.

### 2. Locking / scan cost

- `ALTER COLUMN … SET NOT NULL` acquires `ACCESS EXCLUSIVE` on `orders` and performs a full table verification scan (PostgreSQL 12+ can skip the scan only if a *valid, proven* `CHECK (col IS NOT NULL)` exists — none is present; the schema note says "No CHECK constraint exists on orders.status").
- Table is 4,812,004 rows on a live table receiving "~400 inserts per minute at peak" (from `context.md`), no maintenance window, migration runs automatically inside a single transaction during deploy. That guarantees an outage-sized lock hold and blocks autovacuum, reads, and writes.

An `ACCESS EXCLUSIVE` lock also forces every in-flight query to the back of the queue, including the ones from finance reporting and `orders/list.ts:23`.

### 3. Consumers that depend on NULL

From `application-usage.md`:
> Finance reporting queries `orders.status` and treats `NULL` as "unknown"; it is expected to keep producing rows with `NULL` status until the importer is retired.

> `src/api/orders/report.ts:41` — groups by `status`, includes a `null` bucket in the dashboard.

> `src/imports/legacy-shop.ts:12` — legacy importer, inserts rows without `status`.

NON-NULL status breaks the importer (`INSERT` without `status` → error) and silently changes the report (no NULL bucket; "unknown" disappears). The developer did not update these paths. The "Not searched" note about the fulfilment worker lagging is also unanswered — if the worker lags behind the API deploy, new rows will still sit at NULL and hit constraint errors.

---

## Required changes before merge

1. **Resolve the data, don't assume.** Backfill the 36,117 historical NULL rows to an explicit sentinel (e.g. `'unknown'`, or whatever finance reporting will be moved onto). Do not use empty string — the notes say *"No application code writes an empty string"* anyway, and `''` is a poor sentinel for a `varchar` field. Backfill in batches to avoid a single giant UPDATE.

2. **Change the write path first.** Make `create.ts` insert a non-NULL value (or update the fulfilment worker contract) and deploy *that* before anything reaches production. Similarly update `legacy-shop.ts` to insert a value. The migration and the code must be ordered — rolling deploy: ship the code change first, verify no new NULLs are being created, then ship the schema migration.

3. **Use the safe constraint sequence** in `migration.sql`:
   ```sql
   ALTER TABLE orders
     ADD CONSTRAINT orders_status_not_null
     CHECK (status IS NOT NULL) NOT VALID;

   ALTER TABLE orders
     VALIDATE CONSTRAINT orders_status_not_null;   -- SHARE UPDATE EXCLUSIVE, not ACCESS EXCLUSIVE

   ALTER TABLE orders
     ALTER COLUMN status SET NOT NULL;             -- uses existing valid CHECK; re-scan avoided
   ```
   `VALIDATE` runs with `SHARE UPDATE EXCLUSIVE` and allows concurrent reads/writes. Then `SET NOT NULL` can use the validated constraint and skip a full scan.

4. **Do not put the long backfill inside the same deploy transaction.** The deploy step applies migrations inside a single transaction; batching the backfill in separate, idempotent steps (or a job) avoids holding the deploy.

5. **Address finance reporting / dashboard before shipping the constraint.** Either move them to a sentinel value, or document the intentional change. Since the note says NULL is *expected* until importer retirement, the constraint must come **after** the importer and reporting are changed.

---

## Residual risk if the above are done

- The validated CHECK plus `SET NOT NULL` still requires `ACCESS EXCLUSIVE` briefly at the final `SET NOT NULL` step (metadata-only, but still takes `ACCESS EXCLUSIVE` for a moment). With 400 inserts/minute and lightweight (no scan) enforcement, this is acceptable — but schedule it outside a peak and be prepared for the lock-wait queue.
- The fulfilment worker deployment lag: if the worker can be older than the API, confirm the worker forces status in its INSERT path or a `DEFAULT` is added, otherwise rows could reach the constraint before the worker updates them.
- The constraint name persists after `SET NOT NULL`; keep it to prevent future regression.

**Summary:** The statement fails on deploy because 36,117 NULLs exist, and would be unsafe even after backfill because the API creates new NULLs until the fulfilment worker catches up. Requires write-path changes + `NOT VALID`/`VALIDATE`/`SET NOT NULL` sequencing, with the legacy importer and finance reporting updated first.