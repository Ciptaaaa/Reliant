# Migration Guard Report — PG-005

## Migration Under Review

`db/migrations/0088_events_duration_ms_int.sql`

```sql
ALTER TABLE events ALTER COLUMN duration_ms TYPE integer;
```

## Verdict

**HIGH — Do not merge as written.**

The migration narrows `bigint → integer` on a column that provably contains out-of-range values. PostgreSQL will abort the `ALTER` (it does not truncate), so no data is destroyed — but the deployment fails, and even after the data issue is fixed the statement forces a full table rewrite on ~48M rows. Both data and operational risk are real and recoverable, which is exactly the `HIGH` profile, not `BLOCKER`. Any report calling this `LOW` "because integer is smaller" is incorrect.

## Evidence

**1. Out-of-range values exist in production data (direct quote from `schema.sql`):**
> `SELECT max(duration_ms), min(duration_ms) FROM events;  -> 4212849000, 0`
> `SELECT count(*) FROM events WHERE duration_ms > 2147483647;  -> 3,842`

`max = 4,212,849,000` exceeds `INTEGER` max (`2,147,483,647`) by ~2×. There are **3,842 rows** that cannot be represented. The `ALTER` will fail with a "value out of range for type integer" error at conversion time.

**2. The writer that produces these values is still active in the schema's stated source (from `application-usage.md`):**
> `duration = (ended_at - started_at).total_seconds() * 1000  # can exceed 2^31 for multi-week sessions`
> → historical importer, writes values up to ~4.2e9.

So this is not just legacy cruft — the importer writes values the new type cannot hold. Narrowing the column to `integer` would also **break the importer's writes** once past the range (it would start throwing `numeric value out of range`), not just the migration.

**3. The developer's justification only covers one writer:**
> "The mobile SDK never sends more than 60 seconds, so integer is plenty."

That is true for `ingest.py`, but the case explicitly documents a **second** writer (`import_sessions.py`) that the SDK argument does not address. The premise is incomplete.

**4. Missing `USING` clause is not the failure cause here.** For `bigint → integer` PostgreSQL applies an implicit cast and performs range checking; omitting `USING` is fine. A `USING` clause would **not** rescue this — a cast that exceeds range still errors. Adding `USING duration_ms::integer` changes nothing behaviorally; the only fix is to deal with the data.

**5. Operational cost:** the migration rewrites the full table (~48M rows). `application-usage.md` and `schema.sql` both note the table is append-heavy with a 90-day nightly retention job. An `ACCESS EXCLUSIVE` lock during rewrite on a hot event-log table is a large blast radius. (Note: the `>int` value set — 3,842 rows — may shrink over 90 days as the nightly job deletes old rows. **Re-running the count immediately before deploy is mandatory**, as the developer's 2026-11-16 numbers are a snapshot.)

**6. Reads are not a correctness problem for a successful migration** — serializers return `duration_ms` as an integer in JSON, and the dashboard casts to `numeric` for division. If the type change were viable, reads would adapt. The blockers are writers/data and operations, not readers.

## Required Fixes Before Merge

**Option A — drop the narrowing; keep `bigint` (recommended).**
The stated goal is "match the mobile SDK's declared type." A column type is not required to mirror a client's wire type; `bigint` accepts all SDK values up to 60,000 and all importer values. The cost of `bigint` over `integer` is 4 bytes/row (~192 MB across 48M rows) — far cheaper than a rewrite and a data-coercion job. **This migration provides little benefit for real risk.**

**Option B — if `integer` is truly required, do it in a non-narrowing, staged way:**
1. **Backfill/reconcile out-of-range values first.** `import_sessions.py` writes up to 4.2e9; either clamp, split, or re-model those rows. A **constraint must be added first** to catch new writes:
   ```sql
   -- will fail if any current row is out of range — that's the point
   ALTER TABLE events ADD CONSTRAINT events_duration_ms_int_range
     CHECK (duration_ms BETWEEN -2147483648 AND 2147483647) NOT VALID;
   -- validate in a separate, non-locking step
   ALTER TABLE events VALIDATE CONSTRAINT events_duration_ms_int_range;
   ```
2. **Fix the importer** to never write > `2^31-1` (or store duration as `bigint`/`numeric`).
3. **Deploy the type change with `USING`** only after the constraint passes and the importer is patched:
   ```sql
   ALTER TABLE events ALTER COLUMN duration_ms TYPE integer
     USING duration_ms::integer;
   ```
4. **Plan the rewrite for the 48M-row table**: schedule during low traffic, ensure lock timeout (`SET lock_timeout`), consider `pg_repack` or a shadow-column + trigger approach if the `ACCESS EXCLUSIVE` window is unacceptable.

## Checklist

| Check | Result |
|---|---|
| Narrowing type change (`bigint → integer`) | ⚠️ Yes — lossy by definition |
| Existing out-of-range values | ❌ 3,842 rows > `2,147,483,647`; `max = 4,212,849,000` |
| Active writer producing out-of-range values | ❌ `import_sessions.py` up to ~4.2e9 |
| `USING` clause | N/A — would not fix range errors |
| Full table rewrite on large table | ⚠️ ~48M rows, `ACCESS EXCLUSIVE` |
| Deployment model compatible | ⚠️ Rolling deploy + table rewrite lock |
| Reads broken by on-disk type | ✅ No (serializer/dashboard cast adapt) |
| Data destroyed if statement fails | ✅ No — PostgreSQL aborts, does not truncate |

## Bottom Line

**Do not merge.** The migration is a **HIGH**-severity change: it fails on existing data (3,842 rows, max 4.2e9), conflicts with a still-active importer, and rewrites a 48M-row hot table. The developer's rationale only accounts for the SDK writer, not the documented importer. Fix: **retain `bigint`** (Option A), or if `integer` is mandatory, stage it — constraint → fix importer → backfill → `ALTER ... USING` with a planned rewrite window (Option B).