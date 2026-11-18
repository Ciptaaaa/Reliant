# Migration Guard Report

## Scope
- **Migration**: `db/migrations/0088_events_duration_ms_int.sql`
- **Engine**: PostgreSQL 15
- **Inspected**: `migration.sql`, `schema.sql`, `application-usage.md`, `context.md`
- **Not inspected**: the mobile SDK repository (separate project)
- **Unknowns**: none material; the out-of-range row count and the max value are stated in `schema.sql`

## Verdict
**CONDITIONAL** — 3,842 rows exceed the `integer` range so the narrowing change is rejected, and the path that would make it succeed rewrites 48M rows.

## Findings

### F1 — HIGH — type-change
- **Statement**: `ALTER TABLE events ALTER COLUMN duration_ms TYPE integer` in `migration.sql:2` — narrows `bigint` to `integer` across every row, with no `USING` clause
- **Evidence**:
  - `schema.sql:20` — `SELECT max(duration_ms) -> 4212849000`, above the `integer` maximum of 2147483647
  - `schema.sql:21` — `SELECT count(*) ... WHERE duration_ms > 2147483647 -> 3,842`
  - `application-usage.md:11` — `src/events/import_sessions.py:37` writes epoch-derived durations that can exceed 2^31
- **Impact**: PostgreSQL aborts with `value "4212849000" is out of range for type integer` (SQLSTATE 22003) and the transaction rolls back; no value is truncated because the statement is refused atomically
- **Mitigation**: decide whether the historical values are needed. If they are, keep `bigint` and correct the mobile SDK's declared type instead; if not, archive or repair the 3,842 out-of-range rows first, then convert
- **Verification**: `SELECT count(*) FROM events WHERE duration_ms > 2147483647` returns 0 before the change; `\d events` shows `integer` afterwards
- **Approval**: none
- **Reversible**: yes

### F2 — HIGH — lock-and-rewrite
- **Statement**: if the data is repaired and the conversion succeeds, changing the column type rewrites all 48,000,000 rows of `events` under an `ACCESS EXCLUSIVE` lock
- **Evidence**:
  - `migration.sql:2` — a plain `ALTER COLUMN ... TYPE`; `context.md:15` states "The type change rewrites the table"
  - `schema.sql:16` — `events` holds ~48,000,000 rows with 90-day retention
- **Impact**: reads and writes on the event log block for the length of the rewrite, which on 48M rows exceeds a normal deploy window, and the WAL volume lags replicas
- **Mitigation**: avoid the rewrite with the shadow-column pattern — add `duration_ms_int`, backfill in batches, swap under a short lock — or schedule a maintenance window and accept a bounded write pause
- **Verification**: during the backfill inserts into `events` continue; the final swap is measured in seconds
- **Approval**: schedule
- **Reversible**: yes

### F3 — MEDIUM — operational
- **Statement**: after the type change or a shadow-column swap, the planner statistics for `events.duration_ms` are stale until re-analysed
- **Evidence**:
  - `migration.sql:2` — the column type change recreates the column, which invalidates its statistics
  - `application-usage.md:17` — `src/events/aggregate.py:19` computes p50/p95 with SQL `avg()`
  - `application-usage.md:21` — `dashboards/event_latency.sql:12` casts `duration_ms` for division
- **Impact**: latency aggregates and dashboard queries plan badly straight after the change, producing misleading percentiles during the exact period they are watched
- **Mitigation**: run `ANALYZE events (duration_ms);` in the same change window
- **Verification**: `pg_stats` has a fresh `last_analyze` for the column and the p50/p95 queries return stable plans
- **Approval**: none
- **Reversible**: yes

## Safer migration path
1. Question the premise: the mobile SDK sends values under 60,000, but `import_sessions.py:37` writes epoch-derived values up to ~4.2e9. Resizing to match one client breaks the other.
2. Preferred: keep `bigint` and correct the SDK's declared type. Nothing in the database needs to change.
3. If `integer` is required, repair the 3,842 out-of-range rows first and export the originals.
4. Use the shadow-column pattern instead of a rewrite:
   ```sql
   ALTER TABLE events ADD COLUMN duration_ms_int integer;   -- metadata-only
   -- backfill in batches using duration_ms_int IS NULL as the idempotent predicate
   -- swap under a short lock after a verification pass
   ```
5. Run `ANALYZE events (duration_ms_int);` and compare p50/p95 with the pre-change baseline.

## Rollback and recovery
- **Schema rollback**: converting back to `bigint` widens cleanly but triggers another 48M-row rewrite.
- **Data recovery**: a conversion that succeeds via `USING` could clamp values, so export any archived or clamped values before the change.
- **Application rollback**: safe for the API, which reads the column as an integer either way; the legacy importer needs its own check.

## Pre-deploy checklist
- [ ] Decision recorded: keep `bigint` or accept the conversion
- [ ] The 3,842 out-of-range rows exported or repaired
- [ ] Change applied via shadow column, not a full rewrite
- [ ] `ANALYZE` run and p50/p95 compared with the baseline