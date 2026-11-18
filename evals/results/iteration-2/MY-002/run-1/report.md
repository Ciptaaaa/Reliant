# Migration Guard Report — MY-002

## Verdict

**BLOCK — do not merge as written.**

`ALTER TABLE payments MODIFY COLUMN method VARCHAR(24) NOT NULL;` will **fail at deploy time** because the table contains 84,302 rows with `NULL` (per `schema.sql` data facts). Even after those rows are resolved, the migration as written gives MySQL license to pick a copy/rebuild path and hold a metadata lock for the duration, which stalls writes on the primary and increases replica lag for reporting.

## Findings

### 1. Deploy-blocking: existing `NULL` rows

- Evidence (`schema.sql`):
  - `SELECT COUNT(*) FROM payments; -> 11,240,911`
  - `SELECT COUNT(*) FROM payments WHERE method IS NULL; -> 84,302`
  - Table definition: `method VARCHAR(24) DEFAULT NULL`
- Evidence (`application-usage.md`): the bulk-import path can still write `NULL` —
  > "`app/Console/Commands/ImportLegacyPayments.php:71` — bulk insert from the old gateway export; the export omits `method` for 2 columns of legacy data (so `NULL` can still be written by this path)."
- Evidence (`context.md`): "On MySQL there is no PostgreSQL-style `NOT VALID`/`VALIDATE` for `NOT NULL`: adding the constraint validates existing rows at that moment, and it fails if any `NULL` exists."
- Consequence: `MODIFY ... NOT NULL` fails immediately with `ERROR 1138` (Invalid use of NULL value) or equivalent. MySQL DDL implicitly commits, so this is a hard failure mid-deploy with a rolling deploy in progress.

The developer's claim —
> "`payments.method` is always set by the checkout flow, so I want it `NOT NULL`."

— is contradicted by both the data (84k NULLs) and the import path.

### 2. The migration would also miss future `NULL`s

Even if backfilled, the import path can reintroduce `NULL` unless it is changed concurrently. Enforcing the constraint does not modify the importer; you need either a code change (skip/derive `method` on import) or a `DEFAULT`/trigger strategy. This is not yet addressed.

### 3. DDL cost and lock risk

- `context.md`: "No `ALGORITHM=` / `LOCK=` clause in the migration." and "`MODIFY COLUMN` without an explicit `ALGORITHM` may fall back to a copy/rebuild path."
- 11.2M-row table; a rebuild takes a metadata lock (and potentially a table-copy lock) across the operation.
- `context.md`: "Replica reads: reporting queries run on the replica and lag during expensive DDL." Reporting (`app/Reports/MethodBreakdown.php:19`) will see stale data for the duration — tolerable for `COALESCE(method,'unknown')` but the lag must be bounded.

### 4. Reporting is already tolerant (no read-side blocker)

- `app/Reports/MethodBreakdown.php:19` uses `COALESCE(method,'unknown')` — but the `groupBy('method')` still groups the NULL bucket separately; the final report will mix `'unknown'` labels with NULL grouping until backfilled. Minor, but should be verified after deployment.
- Admin show blade displays blank for NULL — no crash. Not a blocker.

## Required actions before merge

1. **Backfill in batches** (out-of-band, not part of this DDL migration), e.g. `UPDATE payments SET method='unknown' WHERE method IS NULL LIMIT 5000;` looped until 0, on the **primary**, with replication-lag awareness (sleep between batches, monitor `SHOW REPLICA STATUS`). Choose the sentinel value deliberately — `'unknown'` matches the reporting coalesce.
2. **Fix the importer** (`ImportLegacyPayments.php:71`) so the two legacy columns always produce a non-NULL `method` (map or sentinel), deployed *before or with* the constraint.
3. **Then** apply the constraint with an explicit algorithm:
   ```sql
   ALTER TABLE `payments`
     MODIFY COLUMN `method` VARCHAR(24) NOT NULL,
     ALGORITHM=INPLACE, LOCK=SHARED;
   ```
   (`INPLACE` may still fail depending on the charset/collation conversion; if it does, use a maintenance window with `ALGORITHM=COPY, LOCK=SHARED` and accept the lag budget.)
4. **Verify post-change**: `SELECT COUNT(*) FROM payments WHERE method IS NULL;` must be `0`.
5. **Monitor** metadata lock waits and replica lag during the DDL; schedule outside the reporting heavy-read hours.

## Summary

| Check | Result |
|---|---|
| Existing data satisfies constraint | ❌ 84,302 NULL rows |
| Each write path sets `method` | ❌ import path can NULL |
| Explicit `ALGORITHM`/`LOCK` | ❌ none |
| Safety for rolling deploy OK | ❌ DDL fails and blocks |
| Read paths tolerant | ✅ (COALESCE) |

**Decision: REJECT until batched backfill + importer fix + explicit INPLACE/SHARED (or maintenance window) are in place.**