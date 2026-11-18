# Migration Guard Report — PG-002 `orders_status_not_null`

## Verdict: **BLOCK — do not merge as written**

The migration will fail on deploy today (it contradicts the data), and even if it didn't, the lock it takes would stall writes on a busy table with no maintenance window. Ship it only after the rewrite below.

## Evidence

**1. The column is not actually required — there are `NULL` rows, by design.**

`schema.sql` data facts:

> `SELECT count(*) FROM orders WHERE status IS NULL;    -> 36,117`

and `application-usage.md`, Writes:

> `await prisma.order.create({ data: { customerId, status: null, totalCents } });`
> → **new orders are inserted with `status = null`** until the fulfilment worker sets it.

So the developer's claim ("the column already has values for all new orders") is false in the sense that matters: `create.ts:18` deliberately inserts `NULL`, and the fulfilment worker only later runs `UPDATE … SET status = 'fulfilled'`. New rows are `NULL` between insert and fulfilment. The legacy importer (`imports/legacy-shop.ts:12`) also inserts no `status`.

**2. The migration will error, not just be risky.**

For a PostgreSQL 15 metadata-only `SET NOT NULL`, the column must be proven free of `NULL`s. With 36,117 `NULL` rows plus a live producer of new `NULL`s, the migration fails with `column "status" ... contains null values`.

**3. Locking and scan cost.**

`ALTER TABLE orders ALTER COLUMN status SET NOT NULL` takes `ACCESS EXCLUSIVE` on `orders` (it blocks reads *and* writes). On PG 12+ it avoids a full heap scan only because it relies on a valid, non-partial `CHECK` — and there is none (`No CHECK constraint exists on orders.status`). So the statement has to scan the whole table to prove the constraint, under `ACCESS EXCLUSIVE`, on a table with **4,812,004 rows** and **~400 inserts/min at peak**, with **no maintenance window** and the migration running automatically in a single transaction during a rolling deploy. Result: a write stall of unpredictable length on `orders`, plus the failure above.

**4. Consumer expectations.**

`application-usage.md`, Consumers:

> Finance reporting … treats `NULL` as "unknown"; it is expected to keep producing rows with `NULL` status until the importer is retired.

So `NULL` is a legitimate, relied-upon value for some rows even after a code change; making the column `NOT NULL` is a semantic change to a downstream contract, not just a schema tidy-up.

## The `NULL` problem is a policy problem, not a backfill

Before touching `SET NOT NULL` you must decide what `NULL` *means*. There are at least three sources:

- **in-flight rows** from `create.ts:18` before the fulfilment worker runs,
- **historical imports** (pre-2023-06),
- **out-of-repo** finance consumption of `NULL` as "unknown".

A plain `UPDATE orders SET status = '...'` backfill changes finance's "unknown" data and will race the ongoing inserts. You need a deliberate value for "unfulfilled/unknown" (e.g. `'pending'`), and every write path must be updated **before** the constraint lands — including `create.ts` and the legacy importer, and the fulfilment worker must **not** lag behind (context: "may lag … behind the API").

## Rewrite: safe `NOT NULL` sequence

`SET NOT NULL` can be made metadata-only *and* low-lock by adding a *validated* `CHECK` first (PG 12+ uses a valid non-partial `CHECK IS NOT NULL` as proof and skips the heap scan at `SET NOT NULL` time). But note: `VALIDATE CONSTRAINT` still needs `SHARE UPDATE EXCLUSIVE` and **does scan the table**, so it must still be run only once no `NULL`s can occur — otherwise it fails. The sequence:

**Step 0 — app + data readiness (separate deploy, before the constraint).**
- Change `create.ts:18` and `legacy-shop.ts:12` to write a non-`NULL` sentinel (e.g. `'pending'`) — decide this with finance, since `NULL` is their "unknown". Either keep `NULL` semantics by only running the constraint once the importer is retired (see below), or migrate "unknown" to an explicit value and update finance.
- Confirm the fulfilment worker's deployed version no longer relies on `NULL` and no longer inserts/updates `status` to `NULL`.
- Backfill existing rows if you chose a value: `UPDATE orders SET status = 'pending' WHERE status IS NULL;` — **batched**, e.g. 5–10k rows per transaction with a short `lock_timeout`, to avoid a long `ACCESS EXCLUSIVE` and to stay within row-version churn (VACUUM) budgets. Do not run as one giant `UPDATE`.

**Step 1 — add the constraint as `NOT VALID` (fast, low-lock).**

```sql
ALTER TABLE orders
  ADD CONSTRAINT orders_status_not_null
  CHECK (status IS NOT NULL) NOT VALID;
```

`NOT VALID` only needs `ACCESS EXCLUSIVE` for an instant catalog change; it does not scan rows and does not block concurrent writes for long. It applies to **new and updated rows only**, which is exactly what you want once the app writes non-`NULL`.

**Step 2 — validate it (scans the table, lighter lock).**

```sql
ALTER TABLE orders VALIDATE CONSTRAINT orders_status_not_null;
```

Do this **only after** the backfill is complete and no writer can produce `NULL`. `VALIDATE` takes `SHARE UPDATE EXCLUSIVE` (does not block `INSERT`/`UPDATE`/`DELETE`, but blocks other DDL/VACUUM). On a large table this is a long read; it's fine for writes, but consider a quiet period. If any `NULL` slipped in, this fails safely and you retry.

**Step 3 — flip the column flag (metadata-only now).**

```sql
ALTER TABLE orders ALTER COLUMN status SET NOT NULL;
```

Because `orders_status_not_null` is now valid, PG 15 takes the fast path: `ACCESS EXCLUSIVE` but **no heap scan**. Still, set a `lock_timeout` first in the same transaction to fail fast rather than queue behind a long transaction:

```sql
BEGIN;
SET LOCAL lock_timeout = '5s';
ALTER TABLE orders ALTER COLUMN status SET NOT NULL;
COMMIT;
```

**Step 4 (optional) — you may keep or drop `orders_status_not_null`.** `SET NOT NULL` is now enforced by the column itself. Dropping the `CHECK` later is cheap, but if you keep it you have redundant enforcement; most teams keep it to make the `NOT NULL` provable if the column is ever dropped/re-added. Dropping it also does not re-trigger a scan. Decide as a team; not urgent.

## Ordering and deploy constraints

- **Single transaction caveat:** the deploy applies the migration file in one transaction. `VALIDATE CONSTRAINT` inside that transaction will still hold its lock until commit — fine for a light-lock validate, but do **not** put the batched backfill and `VALIDATE` in the same auto-deploy step, because the backfill is job-worthy, not migration-worthy for millions of rows. Split into: (a) migration adds `NOT VALID`, (b) a separate job backfills + validates, (c) migration flips `SET NOT NULL`.
- **Rolling deploy:** the `NOT VALID` constraint applies to new rows immediately. Ensure the *old* app version still running during rollout does not insert `NULL` — if it does, those rows will violate the constraint and the old pods' inserts will fail. This is why Step 0 must precede Step 1 in a separate deploy.
- **Fulfilment worker lag:** the constraint is only safe once the worker no longer expects/creates `NULL` as "not yet fulfilled". If the worker lags, change `create.ts` to insert the sentinel *and* make the worker tolerant of it in the same release.

## Summary of what's shipping now vs. the rewrite

| | Current `migration.sql` | Rewrite |
|---|---|---|
| Succeeds with 36,117 `NULL`s | No — errors | Yes, after backfill |
| Survives live `NULL` inserts (`create.ts:18`) | No | Only after app change |
| Lock on `orders` | `ACCESS EXCLUSIVE` + full scan | Instant `NOT VALID`; light-lock `VALIDATE`; metadata-only `SET NOT NULL` with `lock_timeout` |
| Respects finance's `NULL` = "unknown" | No | Only if policy decided before backfill |

**Do not merge the single-line migration.** Implement the `NOT VALID` → validate → `SET NOT NULL` sequence, and first decide the status value for in-flight and imported rows with finance and the fulfilment-worker owner.