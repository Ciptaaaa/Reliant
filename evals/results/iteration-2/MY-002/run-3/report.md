# Migration Guard Report — MY-002

**Migration:** `ALTER TABLE payments MODIFY COLUMN method VARCHAR(24) NOT NULL;`
(`database/migrations/2026_11_16_121500_payments_method_not_null.php`)

**Verdict: BLOCK.** Do not merge or deploy as written. The statement is guaranteed to fail on the current data, and its failure mode on MySQL is an implicit-commit DDL that rebuilds a 11.2M-row table while holding a metadata lock on the primary and stalling the replica.

---

## 1. Blocking findings

### 1.1 Existing `NULL` rows make the migration fail

Evidence (`schema.sql`, "Data facts"):
> `SELECT COUNT(*) FROM payments WHERE method IS NULL;    -> 84,302`
> "The NULL rows predate 2023-02 (previous gateway import). No CHECK constraint exists on method."

Evidence (`application-usage.md`, Writes):
> `ImportLegacyPayments.php:71` — bulk insert from the old gateway export; the export omits `method` for 2 columns of legacy data (so `NULL` can still be written by this path).

The developer's claim is contradicted by the usage notes. Their premise was:
> "`payments.method` is always set by the checkout flow, so I want it `NOT NULL`."

That is true for `CapturePayment`, but not for the import path. Today there are two ways to get `NULL`: the existing 84,302 legacy rows, and future import runs that use the same path (see §1.4).

MySQL validates every existing row when you add `NOT NULL`. With 84,302 `NULL` rows the `ALTER` fails with `Error 1138: Invalid use of NULL value` (or 1265 depending on strict mode). Because MySQL DDL implicitly commits (`context.md`: "MySQL DDL implicitly commits"), a failure mid-statement leaves no partial state to roll back and can leave the table in the rebuilt-but-unmodified state.

---

### 1.2 The DDL is not instant: it is a full table rebuild or copy

`MODIFY COLUMN` with an explicit `NOT NULL` change is performed as `ALGORITHM=COPY` or a rebuild, not pure metadata. The migration has:
> "No `ALGORITHM=` / `LOCK=` clause in the migration."

Without an explicit clause, MySQL chooses the available algorithm. On a `VARCHAR(24)` column with 11M rows and secondary index `payments_order_id_idx`, this means:
- Rebuild or copy of the clustered index (~11M rows), plus rebuilding `payments_order_id_idx`.
- A metadata lock on the table for the column change: readers and writers to `payments` block for the duration of the copy if the algorithm requires it (the copy path takes shared or exclusive locks depending on version; `INPLACE` takes a brief exclusive metadata lock for table definition, then allows DML but is still a full table rebuild under the hood).

This is consistent with the case background:
> "On MySQL there is no PostgreSQL-style `NOT VALID`/`VALIDATE` for `NOT NULL`: adding the constraint validates existing rows at that moment... `MODIFY COLUMN` without an explicit `ALGORITHM` may fall back to a copy/rebuild path. The agent must... recommend... rather than claiming the operation is instant."

The developer did not claim it is instant, but also did not specify an algorithm. Given the size, the deploy is either a long lock/copy or an outright failure. Neither is acceptable for a rolling deploy.

---

### 1.3 Replica lag and reporting impact

`application-usage.md`, Replication:
> "The replica is used for reporting; large DDL on the primary produces replication lag."

`MethodBreakdown.php:19`:
> `DB::table('payments')->selectRaw("COALESCE(method,'unknown') AS method, COUNT(*) c")->groupBy('method');` → "reporting on the **replica**, tolerant of `NULL`."

The rebuild generates a large amount of binary log traffic. While the replica applies it, `MethodBreakdown` can lag behind or fail on timeouts. The query itself tolerates `NULL` today, but it will be reading a table that is being rewritten on the primary.

---

### 1.4 The import path will re-introduce `NULL` unless changed

Even after backfill and `NOT NULL`, `ImportLegacyPayments.php:71` still writes `NULL` for "2 columns of legacy data." With `NOT NULL` in place, the next import run will throw and the whole bulk insert can fail (or, in non-strict mode, coerce to `''`). The change is not stable without fixing this write path.

---

## 2. Secondary findings

- **No CHECK constraint exists on `method`.** Nothing else enforces the intended domain. `VARCHAR(24)` allows `''`, arbitrary gateway strings, and case variants. This is out of scope for the `NOT NULL` change but worth flagging if `method` is meant to be an enum-like set.
- **Not searched:** "The legacy gateway export file contents (external)." If the export's 2 omitted columns have a recoverable value (e.g., a default method or a sentinel), backfill can be done from real data rather than a placeholder. The report cannot resolve this.
- **Display behavior:** `resources/views/admin/payments/show.blade.php:33` shows an empty string for `NULL`. Backfilling to `'unknown'` changes display for 84K rows. That is likely desired, but should be a product decision, not a silent side effect.

---

## 3. Required remediation before this can be approved

1. **Decide the backfill value.** This is a product/data decision, not a pure migration step. Options: `'unknown'`, `'legacy'`, or a real value recovered from the export. `MethodBreakdown` already uses `'unknown'` as the sentinel, so `'unknown'` is the consistent choice if the export has no usable value. Document the decision.

2. **Run a batched backfill, not one `UPDATE`.**
   A single `UPDATE payments SET method='unknown' WHERE method IS NULL` touches 84,302 rows but scans/locks broadly and generates a large redo/binlog burst, contributing to the same replica lag problem. Use small batches:
   ```sql
   UPDATE payments SET method = 'unknown' WHERE method IS NULL ORDER BY id LIMIT 1000;
   -- repeat until ROW_COUNT() = 0
   ```
   Run from a queue job or via `chunkById`, with a short sleep between batches. Monitor replica lag between batches. This is idempotent (`WHERE method IS NULL`) and safe to resume.

3. **Verify zero NULLs before the DDL.** The migration should not rely on "I checked it earlier." Make the DDL statement itself conditional or verified immediately before:
   - Re-run `SELECT COUNT(*) FROM payments WHERE method IS NULL;` and confirm 0.
   - Optionally, put a guard in the migration that throws if the count is > 0 (better than a failed DDL).

4. **Specify the algorithm explicitly.** Once there are no `NULL` rows, the `NOT NULL` change on `VARCHAR(24)` can be in-place:
   ```sql
   ALTER TABLE payments
     MODIFY COLUMN method VARCHAR(24) NOT NULL DEFAULT NULL,
     ALGORITHM=INPLACE, LOCK=SHARED;
   ```
   Note the `DEFAULT NULL` removal interaction: `MODIFY COLUMN ... NOT NULL` without `DEFAULT` will drop the current implicit default. If you want the column to have no default (the correct state), leave it as written but be explicit. Requirements:
   - Confirm `ALGORITHM=INPLACE` is actually chosen. Run `ALTER ... ALGORITHM=INSTANT` first: MySQL 8.0.36 does not allow `INSTANT` for `NOT NULL` on an existing column in general, but `INPLACE` is the safe target.
   - If `INPLACE` is rejected (e.g., due to collation/charset mismatch with the table), the DDL falls back to copy. In that case, schedule a maintenance window. Do not let the fallback happen silently during a rolling deploy.
   - `LOCK=SHARED` permits concurrent reads but blocks writes for the short DDL. On 11M rows this is still a window; coordinate with the queue worker and API.

5. **Fix or gate the import path.** Before the constraint is enforced, update `ImportLegacyPayments.php:71` to supply a non-`NULL` default for the 2 missing columns, or make the import fail loudly and stop, rather than relying on the DB to reject it. Otherwise the same regression re-appears after the migration.

6. **Maintenance window / rollout plan.** Given:
   > "No `ALGORITHM=` / `LOCK=` clause... Replica reads: reporting queries run on the replica and lag during expensive DDL."

   Stage this as: (a) backfill complete and verified; (b) import path fixed; (c) deploy the `ALTER` with explicit `ALGORITHM=INPLACE, LOCK=SHARED` during low traffic, with replica lag monitored; (d) confirm the replica has caught up before relying on reporting.

---

## 4. Summary

| Check | Result |
|---|---|
| Existing `NULL` rows | **Fail**: 84,302 |
| DDL is metadata-only | **No**: rebuild/copy of 11M rows |
| Explicit `ALGORITHM`/`LOCK` | **Missing** |
| Write path keeps `NULL` out | **No**: import path writes `NULL` |
| Replica impact | High: binlog burst + lag |
| Reporting query tolerant of `NULL` | Yes, but not of replica lag |
| Verdict | **BLOCK** |

The migration is not safe to merge. It will fail on production data, and even after a manual backfill it is an expensive rebuild with no algorithm and no lock-level control. The developer's underlying intent (enforce `NOT NULL`) is reasonable; the mechanism is not.
