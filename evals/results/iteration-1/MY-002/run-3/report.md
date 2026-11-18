# Migration Guard Report — MY-002

## Verdict: **BLOCKED — do not merge as-is**

Two independent blockers: (1) the migration will **fail at deploy** because 84,302 `NULL` rows still exist; (2) even after a backfill, the unspecified `ALGORITHM`/`LOCK` on an 11M-row table risks a copy/rebuild with an exclusive table lock and replication lag on the replica. The developer's premise ("`method` is always set") is **false** per the application-usage evidence.

---

## 1. Correctness — the `NOT NULL` will conflict with existing data

**Evidence (schema.sql):**
> `SELECT COUNT(*) FROM payments WHERE method IS NULL;    -> 84,302`

**Evidence (migration.sql):**
> `ALTER TABLE payments MODIFY COLUMN method VARCHAR(24) NOT NULL;`

MySQL has no deferred validation for `NOT NULL` (context.md confirms: *"adding the constraint validates existing rows at that moment, and it fails if any `NULL` exists"*). With 84,302 existing `NULL`s, this statement **errors out** and the migration aborts. The rolling deploy halts mid-rollout.

**The developer's claim is contradicted by the evidence.** They said the checkout flow always sets `method`, but writes are not the only source:

**Evidence (application-usage.md, Writes):**
> `app/Console/Commands/ImportLegacyPayments.php:71` — bulk insert from the old gateway export; the export omits `method` for 2 columns of legacy data (so `NULL` can still be written by this path).

And the existing rows are precisely of that origin:

**Evidence (schema.sql):**
> The `NULL` rows predate 2023-02 (previous gateway import).

So `NULL` is not merely legacy residue — a live write path can still produce it.

Additionally, `show.blade.php:33` currently renders `{{ $payment->method }}` and is *expected* to be empty for `NULL`. After a backfill, those rows will display a sentinel value instead — the product decision on *what* to display needs to be made with the view owner, not silently by a string default.

## 2. Write-path gap that survives the backfill

Backfilling the 84,302 existing rows fixes *today's* data, but **does not** stop future `NULL`s from `ImportLegacyPayments` if that path is not also fixed. The ordering requirement is:

1. Fix `ImportLegacyPayments.php:71` to supply a non-`NULL` value (or to reject/skip rows that would insert `NULL`), **or** add a `CHECK (method IS NOT NULL)` in a prior migration that is validated *after* the backfill.
2. Backfill the existing rows.
3. *Then* add `NOT NULL`.

Otherwise the constraint can be violated again the moment someone re-runs the legacy import.

## 3. DDL cost, metadata lock, and replication lag

**Evidence (context.md):**
> No `ALGORITHM=` / `LOCK=` clause in the migration.

**Evidence (application-usage.md, Replication):**
> The replica is used for reporting; large DDL on the primary produces replication lag.

Without an explicit `ALGORITHM=INPLACE, LOCK=SHARED`, the server *may* fall back to a copy/rebuild on an 11M-row table. `MODIFY COLUMN ... NOT NULL` on a `VARCHAR` whose nullability changes is a metadata-only change *in principle* on 8.0 (INSTANT is not available for changes that alter nullability in a way that triggers rebuild semantics; it is generally INPLACE but can be forced to COPY by unrelated factors such as column reorder or charset). On InnoDB 8.0.36 `MODIFY COLUMN` affecting only nullability is typically INPLACE/rebuild-free, **but the case requires us not to assume it** — and the case record (context.md) explicitly flags this:

> The agent must find the `NULL` rows, the deploy failure, and the metadata-lock/rebuild cost, and recommend a batched backfill plus an explicit `ALGORITHM=INPLACE, LOCK=SHARED` (or a maintenance window) rather than claiming the operation is instant.

Relying on implicit defaults is not acceptable for an 11M-row table on a single primary with a lag-sensitive reporting replica.

## 4. Implicit commit (rollout safety)

**Evidence (context.md):**
> MySQL DDL implicitly commits.

The `MODIFY` cannot be wrapped in a transaction; if the migration fails at the backfill step or the ALTER fails mid-deploy, the DB is in a half-migrated state. The migration must be **idempotent or resumable** (e.g., `information_schema.COLUMNS` check before ALTER, batched backfill with a `WHERE method IS NULL` guard).

---

## Required changes before merge

1. **Backfill first, in batches.** e.g.
   ```sql
   UPDATE payments SET method = 'unknown' WHERE method IS NULL LIMIT 5000; -- repeat until 0 rows
   ```
   Batch size tuned to replica lag; throttle between batches. `COALESCE(method,'unknown')` in `MethodBreakdown.php` already implies `'unknown'` is the accepted sentinel for reporting — confirm with the report/view owners. Run this as a separate, idempotent, non-transactional step.

   > Note: the batch size must be small enough that replication lag does not spike; the replica serves reporting, so this is a workload decision, not just a DML one.

2. **Fix the write path** (`ImportLegacyPayments.php:71`) so it cannot insert `NULL`, before or concurrently with the backfill.

3. **Order the validation:**
   - After confirming `SELECT COUNT(*) ... WHERE method IS NULL` returns 0,
   - run the ALTER with explicit safety:
   ```sql
   ALTER TABLE payments MODIFY COLUMN method VARCHAR(24) NOT NULL, ALGORITHM=INPLACE, LOCK=SHARED;
   ```
   If `ALGORITHM=INPLACE` is rejected on this version/configuration, fall back to a scheduled maintenance window rather than letting it silently COPY.

4. **Decide the display sentinel** in `show.blade.php:33` deliberately (recommend replacing `{{ $payment->method }}` with `{{ $payment->method ?? 'unknown' }}` to keep UI consistent with the report).

5. **Validate the backfill count before the ALTER** — the migration must abort cleanly if any `NULL` remains.

---

## Summary of evidence relied on

| Concern | Evidence | Status |
|---|---|---|
| Deploy fails on existing `NULL`s | schema.sql: 84,302 `NULL` rows | BLOCKER |
| `NULL` can still be written | application-usage.md: `ImportLegacyPayments.php:71` | BLOCKER for post-migration integrity |
| Rebuild / lock risk | context.md: no `ALGORITHM`/`LOCK`, replica lag | BLOCKER (must specify) |
| Implicit commit / recovery | context.md: MySQL DDL commits | Requires idempotent steps |
| NULL-tolerant UI today | application-usage.md: `show.blade.php:33` | Product decision needed |

**Verdict: BLOCKED.** The migration is not safe to merge until the backfill, write-path fix, and explicit `ALGORITHM=INPLACE, LOCK=SHARED` (or maintenance window) are in place.