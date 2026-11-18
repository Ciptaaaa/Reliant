# Migration Guard Report — MY-002

**Migration:** `database/migrations/2026_11_16_121500_payments_method_not_null.php`
```sql
ALTER TABLE `payments` MODIFY COLUMN `method` VARCHAR(24) NOT NULL;
```

**Verdict: ❌ BLOCK — do not merge as written.** This migration will fail on deployment against production data, and even after a backfill it is written in a way that forces a DDL path with unacceptable lock and replica-lag cost on an 11M-row table.

---

## Findings

### 1. 🔴 GUARANTEED DEPLOY FAILURE: `NULL` rows exist in `payments.method`

**Evidence (schema.sql, Data facts):**
> `SELECT COUNT(*) FROM payments WHERE method IS NULL; -> 84,302`
> "The NULL rows predate 2023-02 (previous gateway import). No CHECK constraint exists on method."

**Evidence (context.md):**
> "On MySQL there is no PostgreSQL-style `NOT VALID`/`VALIDATE` for `NOT NULL`: adding the constraint validates existing rows at that moment, and it fails if any `NULL` exists."

The `ALTER TABLE ... MODIFY COLUMN method ... NOT NULL` statement validates every existing row. With **84,302 `NULL` values** present, MySQL will reject the change with an error such as `ERROR 1138 (22004): Invalid use of NULL value` (or `1265 Data truncated`). This is not a maybe — the migration will fail at deploy time.

**Note the deception in application-usage.md:** the checkout write path always sets `method`:
> `app/Services/Checkout/CapturePayment.php:52` → "always sets `method` for new payments."

…but a second write path does not guarantee it, and is the source of the nulls:
> `app/Console/Commands/ImportLegacyPayments.php:71` — "the export omits `method` for 2 columns of legacy data (so `NULL` can still be written by this path)."

So even after backfill, this migration is **not durable** unless the import path is fixed (§5).

### 2. 🟠 DDL PLAN IS UNSAFE: no `ALGORITHM`/`LOCK`, forces a copying/rebuilding ALTER

**Evidence (context.md):**
> "No `ALGORITHM=` / `LOCK=` clause in the migration."
> "`MODIFY COLUMN` without an explicit `ALGORITHM` may fall back to a copy/rebuild path."
> "MySQL DDL implicitly commits."

The statement is also not detected as a no-op. Changing `DEFAULT NULL` → `NOT NULL` is itself a nullable-to-non-nullable change. MySQL 8.0 does **not** support `ALGORITHM=INSTANT` for this (INSTANT is only for nullable→nullable or certain default changes); without explicit hints the optimizer may choose `COPY`, which:
- rebuilds the full ~11M-row table (disk, IOPS, time),
- holds a metadata lock for the entire operation, blocking or queueing writes to `payments`,
- generates large binlog volume, which the **async replica** must replay — see §3.

### 3. 🟠 REPLICA LAG AND REPORTING IMPACT

**Evidence (application-usage.md, Replication):**
> "The replica is used for reporting; large DDL on the primary produces replication lag."

**Evidence (context.md):**
> "Replica reads: reporting queries run on the replica and lag during expensive DDL."

**Evidence (application-usage.md, Reads):**
> `app/Reports/MethodBreakdown.php:19` → "reporting on the **replica**"

A copy/rebuild of an 11M-row table produces binlog events that the async replica must apply. During the ALTER, reporting queries on the replica will either run against a stale dataset or block; both are customer-visible if the report drives billing/ops decisions.

### 4. 🟠 MISSING PRE-MIGRATION DATA STEP

The migration has **no `UPDATE` to resolve the 84,302 `NULL` rows** before adding the constraint. A `NOT NULL` constraint requires that every row has a value at the moment the constraint is validated. There is no:
- `UPDATE payments SET method = 'unknown' WHERE method IS NULL;`
- `ALTER TABLE ... SET DEFAULT ...` (with a sentinel value) and then `UPDATE` nulls.

### 5. 🟡 NOT DURABLE: the legacy import can re-introduce `NULL`s

**Evidence (application-usage.md):**
> `app/Console/Commands/ImportLegacyPayments.php:71` — "the export omits `method` for 2 columns of legacy data (so `NULL` can still be written by this path)."
> "Not searched: The legacy gateway export file contents (external)."

The import path is not covered by "not searched" in a way that excludes it. Because the export itself can contain null/missing `method` values, the import could attempt future inserts that violate the new `NOT NULL` constraint — depending on MySQL's sql_mode, inserts would fail (`STRICT_TRANS_TABLES` → error) or silently coerce to `''`. **This needs to be verified or patched.**

---

## Required changes before merge

### A. Backfill `NULL` values (batched, to avoid a single massive UPDATE)

```sql
-- Repeat in batches of ~10k, keyed by id, until 0 rows affected.
UPDATE payments
SET method = 'unknown'
WHERE method IS NULL
ORDER BY id
LIMIT 10000;
```
- Choose a sentinel that the app doesn't use as a real method (e.g. `'unknown'` — note `MethodBreakdown.php:19` already uses `COALESCE(method, 'unknown')`, so reporting semantics are preserved).
- Monitor replication lag between batches.

**Verify before altering:**
```sql
SELECT COUNT(*) FROM payments WHERE method IS NULL;  -- must return 0
```

### B. Fix the import path or accept the risk

Before adding `NOT NULL`, patch `ImportLegacyPayments.php` to coalesce missing `method` (e.g. `COALESCE(:method, 'unknown')`), or confirm the export's "2 columns" always carry a non-null method. **Evidence not searched:** the external export file.

### C. Use an explicit, safe ALTER (not the bare `MODIFY`)

MySQL 8.0 does **not** support in-place `MODIFY COLUMN` for `VARCHAR(24) NOT NULL` on InnoDB; it will do a `COPY` rebuild. Provide explicit hints so the DDL fails loudly if the engine would otherwise pick a worse path:

```sql
ALTER TABLE `payments`
  MODIFY COLUMN `method` VARCHAR(24) NOT NULL,
  ALGORITHM=COPY, LOCK=SHARED;
```

But this is still a full-table copy + a metadata lock. Because `payments` is 11M rows and the replica is used for reporting, the safe plan is:

- **Option 1 (recommended): maintenance window.** Run the ALTER during a scheduled window when the table is quiescent and replica lag is tolerable. Monitor replica lag during the ALTER.
- **Option 2 (preferred for online operation): `pt-online-schema-change` / `gh-ost`.** This does the rebuild with a shadow table, batched copy, and triggers to keep writes flowing. It naturally drops to near-zero lock at cutover. Verify the result (`SHOW CREATE TABLE payments`) matches the intended schema.

Either way, do **not** merge the bare `MODIFY` with implicit algorithm/lock selection (see §2).

### D. Re-validate after change

```sql
SHOW CREATE TABLE payments;                    -- confirm NOT NULL
SELECT COUNT(*) FROM payments WHERE method IS NULL;  -- 0
```

---

## Summary table

| # | Issue | Evidence | Severity |
|---|-------|----------|----------|
| 1 | 84,302 existing `NULL`s → `NOT NULL` validation fails | schema.sql data facts; context.md | 🔴 Blocker |
| 2 | Bare `MODIFY` → implicit COPY rebuild, metadata lock, commit barrier | context.md; MySQL 8.0 semantics | 🟠 High |
| 3 | Replica lag degrades `MethodBreakdown` reporting | application-usage.md; context.md | 🟠 High |
| 4 | Legacy import can re-introduce `NULL` | application-usage.md `ImportLegacyPayments.php:71` | 🟡 Medium |
| 5 | Silent sql_mode coercion if import runs after change | MySQL strict mode; unverified export | 🟡 Medium |

**Action:** Do not merge. Backfill, fix the import path, apply with an online schema change tool or in a maintenance window, and re-validate.