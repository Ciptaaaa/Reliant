# Migration Guard Report — PG-002

## Verdict

**❌ BLOCK — do not merge as written.**

`ALTER TABLE orders ALTER COLUMN status SET NOT NULL;` will (1) fail outright against current data, and (2) even if it didn't, it would take `ACCESS EXCLUSIVE` on a 4.8M-row table inside the deploy transaction while ~400 inserts/min continue — blocking all reads and writes on `orders` for the duration of a full table scan, with no maintenance window. Replace it with the `CHECK … NOT VALID` → `VALIDATE` → `SET NOT NULL` sequence plus a data-resolution step for the existing `NULL`s.

---

## Evidence

**1. `NULL` rows exist in production.** From `schema.sql`:

> `--   SELECT count(*) FROM orders WHERE status IS NULL;    -> 36,117`
> `-- The NULL rows are historical imports from the legacy shop system (before 2023-06).`

**2. The application deliberately inserts `NULL`.** From `application-usage.md`:

> `await prisma.order.create({ data: { customerId, status: null, totalCents } });`
> → **new orders are inserted with `status = null`** until the fulfilment worker sets it.

**3. Another writer omits `status` entirely.** From `application-usage.md`:

> `src/imports/legacy-shop.ts:12` — legacy importer, inserts rows without `status`.

**4. Downstream expects `NULL` to persist.**

> Finance reporting queries `orders.status` and treats `NULL` as "unknown"; it is expected to keep
> producing rows with `NULL` status until the importer is retired.

**5. Scale and lock mode.** From `schema.sql` / `context.md`:

> `SELECT count(*) FROM orders;  -> 4,812,004`
> "several million rows" … "receives about 400 inserts per minute at peak"
> "No maintenance window. Migration runs automatically as part of the deploy."
> "the migration file is applied inside a single transaction by the deploy step."

`SET NOT NULL` acquires `ACCESS EXCLUSIVE` and, absent a pre-validated `CHECK`, performs a full-table verification scan of ~4.8M rows.

---

## Findings

### 🔴 F1 — Migration will fail on existing data
The column holds **36,117 `NULL` rows**. PostgreSQL will reject `SET NOT NULL` with `column "status" contains null values`, and because Prisma runs each migration in a single transaction, the deploy step will **abort and roll back**. The developer's "the column already has values for all new orders" only addresses new writes — not the historical/importer rows.

### 🔴 F2 — `SET NOT NULL` without a pre-validated `CHECK` locks and scans live traffic
Even after the `NULL`s were resolved, plain `SET NOT NULL` on a table with no existing validated constraint takes `ACCESS EXCLUSIVE` and scans the whole table to prove the invariant. On 4.8M rows during a no-maintenance-window rolling deploy, that means every `SELECT`/`INSERT`/`UPDATE` on `orders` (including the ~400 inserts/min and the `list.ts`/`report.ts` reads) stalls behind the lock for the scan duration — a user-visible outage and a lock-queue pileup risk. This is exactly the case the `CHECK … NOT VALID` → `VALIDATE` (share-update lock, no write block) → `SET NOT NULL` (uses the validated check, no scan) sequence exists to avoid.

### 🔴 F3 — `NOT NULL` contradicts current write paths and downstream contract
Both `src/domain/orders/create.ts` (inserts `status = null`) and `src/imports/legacy-shop.ts` (omits `status`) will start failing immediately after cutover. Finance reporting is documented to rely on `NULL` = "unknown" until the importer is retired. Making the column `NOT NULL` is a **schema + application + cross-team contract change**, not a one-line DDL change.

### 🟡 F4 — Related writer version skew (noted, not blocking on its own)
> "The fulfilment worker's deployment version; it may lag behind the API."

The fulfilment worker does `UPDATE orders SET status = 'fulfilled' …` — that write supplies a value, so it is compatible. But confirm no older deployed worker version performs inserts without `status`; if any do, F3 applies to them too.

### 🟡 F5 — No backfill plan / placeholder semantics undecided
The notes only say the `NULL`s are legacy imports and that no app code writes `''`. There is no decision recorded for what the 36,117 historical rows *should* become (e.g. `'unknown'`), and no plan for the ongoing importer rows. "Backfill alone" is insufficient — it does not remove the lock-scan in F2, and it would silently change finance's `NULL` bucket semantics.

---

## Required changes before merge

1. **Decide the semantics of the historical `NULL`s** with the Finance owner and the importer owner (placeholder value vs. keeping `NULL`). If finance must keep seeing `NULL` until the importer retires, `NOT NULL` cannot ship yet.
2. **Backfill** the 36,117 legacy rows in **batched** updates (e.g. `UPDATE orders SET status = '<placeholder>' WHERE status IS NULL` in chunks, outside the deploy transaction) — never one giant `UPDATE` inside the migration.
3. **Update the write paths**: `create.ts` and `legacy-shop.ts` must set a status value (or be retired) *before* `NOT NULL` lands; deploy those first (rolling deploy ordering).
4. **Replace the DDL** with the safe sequence, in separate migrations:
   ```sql
   -- 1) add without scanning, no write lock
   ALTER TABLE orders
     ADD CONSTRAINT orders_status_not_null_chk
     CHECK (status IS NOT NULL) NOT VALID;

   -- 2) validate (SHARE UPDATE EXCLUSIVE — does not block DML)
   ALTER TABLE orders VALIDATE CONSTRAINT orders_status_not_null_chk;

   -- 3) SET NOT NULL now uses the validated check; no full scan
   ALTER TABLE orders ALTER COLUMN status SET NOT NULL;
   ```
   The existing `orders_status_idx` is fine to keep; it does not substitute for the check-validation step.
5. **Verify** post-migration: `SELECT count(*) FROM orders WHERE status IS NULL;` → 0, and confirm the ~400/min insert load still succeeds.

---

## Summary

| Check | Result |
|---|---|
| `NULL` rows handled | ❌ 36,117 remain → migration fails |
| Lock impact assessed | ❌ `ACCESS EXCLUSIVE` + full scan on 4.8M rows, no window |
| Safe pattern used | ❌ plain `SET NOT NULL`, no `NOT VALID`/`VALIDATE` |
| Write paths compatible | ❌ `create.ts`, `legacy-shop.ts`, finance contract |
| Deploy transaction | ❌ failure rolls back entire deploy step |

**Verdict: BLOCK.** Do not merge `migration.sql` in its current form.