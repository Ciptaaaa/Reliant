# Migration Guard Report — MY-002

## Verdict

**BLOCK**. This migration will fail on deploy and, even after a backfill, carries unaccounted lock and replication cost.

---

## Findings

### 1. The migration will fail immediately — `NULL` rows exist

Evidence (`schema.sql`, data facts):
> `SELECT COUNT(*) FROM payments WHERE method IS NULL;  -> 84,302`
> "The NULL rows predate 2023-02 (previous gateway import). No CHECK constraint exists on method."

The migration:
> `ALTER TABLE payments MODIFY COLUMN method VARCHAR(24) NOT NULL;`

On MySQL there is no `NOT VALID`/`VALIDATE` step for `NOT NULL`. The validation happens at `ALTER` time and aborts on the first `NULL`. With 84,302 `NULL` rows present, this statement fails synchronously. This is a hard deploy failure, not a warning.

### 2. The "always set by checkout" premise is false

Evidence (`application-usage.md`, Writes):
> `app/Console/Commands/ImportLegacyPayments.php:71` — bulk insert from the old gateway export; the
> export omits `method` for 2 columns of legacy data (so `NULL` can still be written by this path).

And under Not searched:
> The legacy gateway export file contents (external).

So a live write path can still produce `NULL`. Adding `NOT NULL` without fixing or gating that path risks new failures after the backfill, and the external export contents were not inspected, so the size of that risk is unknown. This must be resolved (backfill + import-path change or explicit sentinel) before the constraint can be trusted.

### 3. `MODIFY COLUMN` without an explicit `ALGORITHM` — lock/rebuild risk on 11M rows

Evidence (`context.md` / `schema.sql`):
> No `ALGORITHM=` / `LOCK=` clause in the migration.
> `payments` ~11M rows; old rows imported from a previous gateway.

`method` is `VARCHAR(24) DEFAULT NULL` → `VARCHAR(24) NOT NULL`, same charset/collation/length. That is a candidate for in-place metadata-only change, but with no explicit `ALGORITHM=INPLACE, LOCK=SHARED`, MySQL is free to choose the copy/rebuild path, which holds locks and can stall writes. The migration does not declare its intent, so its cost is undefined until run.

### 4. Replica lag during DDL

Evidence (`application-usage.md`, Replication / context):
> "The replica is used for reporting; large DDL on the primary produces replication lag."
> "Replica reads: reporting queries run on the replica and lag during expensive DDL."

Rolling deploy + an unqualified `ALTER` on an 11M-row table can push replica lag. Reporting already tolerates `NULL` (see below), so the reporting correctness is not at risk, but the lag itself is the operational risk.

### 5. Reads are tolerant — no read-side breakage from existing `NULL`s

Evidence:
> `app/Reports/MethodBreakdown.php:19` → `COALESCE(method,'unknown')` on the replica, "tolerant of NULL".
> `resources/views/admin/payments/show.blade.php:33` — displays `{{ $payment->method }}` (empty for NULL).

No read path relies on `NULL` being distinguishable after the constraint; the view simply shows empty. This means a backfill to a sentinel (e.g. `'unknown'`) is acceptable from the read side, though it changes displayed data for those 84,302 rows — worth confirming with the reporting owner.

---

## Required changes before merge

1. **Backfill first, then constrain.** Batched update of the 84,302 `NULL` rows (e.g. `'legacy_unknown'` or `'unknown'`, matching the report's `COALESCE` sentinel) in small chunks with a bounded `LIMIT`, committed per batch, off-peak.
2. **Fix the import path.** Change `ImportLegacyPayments.php` so the old export cannot write `NULL` (default value or explicit sentinel), or the constraint will fail on the next import. The un-inspected external export must be checked.
3. **Make the `ALTER` explicit and cheap** once `NULL` count is 0:
   ```sql
   ALTER TABLE `payments`
     MODIFY COLUMN `method` VARCHAR(24) NOT NULL,
     ALGORITHM=INPLACE, LOCK=SHARED;
   ```
   Confirm `INPLACE` is actually accepted for this change on 8.0.36 before relying on it; otherwise schedule a maintenance window for the copy path.
4. **Separate the deploy.** Run the backfill (deploy N), verify zero `NULL`s, then ship the `ALTER` (deploy N+1) — not both in one migration.
5. **Watch replica lag** during the `ALTER` window; pause heavy reporting or accept queued reports.

---

## Evidence relied on

- `schema.sql`: `method VARCHAR(24) DEFAULT NULL`; `COUNT(*) = 11,240,911`; `NULL = 84,302`; no `CHECK`.
- `migration.sql`: bare `MODIFY ... NOT NULL`, no `ALGORITHM`/`LOCK`.
- `application-usage.md`: NULL-producing write at `ImportLegacyPayments.php:71`; tolerant read at `MethodBreakdown.php:19`; replica used for reporting; external export not searched.
- `context.md`: no `ALGORITHM`/`LOCK`, replica lag during expensive DDL, implicit commit, no `NOT VALID` on MySQL.

**Summary:** Do not merge as-is. It fails on the 84,302 existing `NULL`s, and even after a backfill the unqualified `MODIFY` risks a locking rebuild on 11M rows with replica-lag impact. Backfill → fix import → explicit `ALGORITHM=INPLACE, LOCK=SHARED` → deploy separately.