# Migration Guard Report — `20261116091500_orders_status_not_null`

## Verdict: 🔴 **BLOCK — do not merge**

This migration will fail (or, on a stale plan, lock a large hot table), and it does not address the data that violates the new constraint.

---

## 1. Data conflict: NULL rows exist

The migration asserts `status IS NOT NULL`, but the data contradicts the developer's claim.

> `-- SELECT count(*) FROM orders WHERE status IS NULL; -> 36,117`
> `-- The NULL rows are historical imports from the legacy shop system (before 2023-06).`

Application evidence corroborates that NULLs are *deliberate and ongoing*, not stale:

> `src/domain/orders/create.ts:18` → **new orders are inserted with `status = null`** until the fulfilment worker sets it.
> `src/imports/legacy-shop.ts:12` — legacy importer, inserts rows without `status`.
> Finance ... "is expected to keep producing rows with `NULL` status until the importer is retired."

**Result:** `ALTER TABLE ... SET NOT NULL` will raise `column "status" of relation "orders" contains null values` and the deploy's single transaction rolls back → **the deploy fails.**

## 2. Lock / performance hazard

Even with NULLs removed first, `SET NOT NULL` on PostgreSQL < 12 takes `ACCESS EXCLUSIVE` and full-scans the table — **blocking all reads and writes for the duration.**

- ~4.8M rows (`SELECT count(*) FROM orders; -> 4,812,004`) → measurable scan time.
- ~400 inserts/min at peak (`context.md`).
- No maintenance window; migration runs automatically inside the deploy transaction (`context.md`).
- PG15 does store a `NOT NULL` catalog flag when a valid `CHECK` exists, but the plain `SET NOT NULL` here still requires a full table scan to verify, all under `ACCESS EXCLUSIVE`.

## 3. Write-path breakage even after backfill

If you only backfill the 36,117 existing rows and then `SET NOT NULL`, the constraint ships **before** the code paths that insert NULL are changed:

- `src/domain/orders/create.ts:18` inserts `status: null` → every new order fails.
- `src/imports/legacy-shop.ts:12` inserts rows without `status` → importer fails.
- Fulfilment worker "may lag behind the API" (`application-usage.md` → Not searched) — an older worker could still assume nullable.

A backfill alone is **not** a sufficient fix; the report explicitly calls this out as the trap.

## 4. Things that are fine

- No empty-string writes: "No application code writes an empty string for `status`." So no need for a `NULLIF`-style cleanup on `''`.
- Index `orders_status_idx` on `status` is unaffected.

---

## Recommended migration sequence

Ship as **two migrations**, and land application changes in between (or make writes tolerate both states).

**Migration A — expand (safe, online):**

```sql
-- 1. Backfill existing NULLs (batched to avoid long lock / bloat spikes)
--    Use the value semantics Finance agreed on, e.g. 'unknown' or 'pending'.
UPDATE orders SET status = 'pending'
WHERE status IS NULL;          -- run in batches of e.g. 10k in a job, not in the deploy txn

-- 2. Add the constraint WITHOUT scanning, validated later
ALTER TABLE orders
  ADD CONSTRAINT orders_status_not_null CHECK (status IS NOT NULL) NOT VALID;

ALTER TABLE orders VALIDATE CONSTRAINT orders_status_not_null;  -- SHARE UPDATE EXCLUSIVE, non-blocking
```

At this point new writes are still allowed to be NULL only if you haven't yet changed the code — so **change the write paths and deploy them first**, then run the above.

**Migration B — flip to real NOT NULL (fast, PG15):**

```sql
ALTER TABLE orders ALTER COLUMN status SET NOT NULL;
-- PG15 sees the validated CHECK and marks NOT NULL without a full scan / long lock
-- (a brief ACCESS EXCLUSIVE lock to update the catalog only).
ALTER TABLE orders DROP CONSTRAINT orders_status_not_null; -- optional cleanup, same lock class
```

**Application changes required before Migration B:**

1. `src/domain/orders/create.ts:18` — insert a real default status (e.g. `'pending'`) instead of `null`.
2. `src/imports/legacy-shop.ts:12` — provide a status (or keep backfilling until importer retired; if you must keep NULLs during the transition, do **not** flip to `NOT NULL` yet).
3. Confirm the fulfilment worker's deployed version does not write/expect NULL (`application-usage.md` → "Not searched").
4. Coordinate with Finance that "unknown" rows will now carry an explicit status value; update the report's `null` bucket.

---

## Evidence summary

| Claim | Source | Bearing |
|---|---|---|
| 36,117 NULL rows exist | `schema.sql` data facts | `SET NOT NULL` will fail |
| New rows insert `status = null` | `create.ts:18` | Even after backfill, writes break |
| Importer inserts without status | `legacy-shop.ts:12` | Ongoing NULL source |
| No maintenance window, deploy txn | `context.md` | Long `ACCESS EXCLUSIVE` lock blocks app |
| 4.8M rows, 400 ins/min | `context.md` | Lock duration non-trivial |
| No empty strings | `application-usage.md` | No `''` cleanup needed |

**Bottom line:** Do not merge as-is. The migration has a data precondition (no NULLs) that current code actively violates, and the single-statement `SET NOT NULL` is unsafe on this table size and write rate. Use `CHECK ... NOT VALID` → `VALIDATE` → `SET NOT NULL`, gated behind write-path changes.