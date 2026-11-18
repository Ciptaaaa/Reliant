# Migration Guard Report — PG-005

**Migration:** `db/migrations/0088_events_duration_ms_int.sql`
**Statement:** `ALTER TABLE events ALTER COLUMN duration_ms TYPE integer;`
**Engine:** PostgreSQL 15

---

## Verdict: **HIGH** — Do not merge as written

This migration will fail at execution time on the production dataset and, even if the data happened to fit, performs a full table rewrite on a 48M-row table. Recoverable (the statement aborts rather than corrupting data), but it blocks the deploy and carries real operational risk.

---

## Findings

### 1. Narrowing conversion with out-of-range values (HARD FAILURE)

`bigint → integer` narrows the representable range to `[-2147483648, 2147483647]`. PostgreSQL checks values during the conversion and aborts if any row is out of range.

Evidence from `schema.sql` (data facts, run 2026-11-16):
> `SELECT max(duration_ms), min(duration_ms) FROM events;` → **`4212849000, 0`**
> `SELECT count(*) FROM events WHERE duration_ms > 2147483647;` → **`3,842`**

`4,212,849,000` far exceeds `2147483647`, and **3,842 rows** exceed the integer range. The `ALTER` will raise `integer out of range` and roll back.

Root cause, from `application-usage.md`:
> `src/events/import_sessions.py:37` — `duration = (ended_at - started_at).total_seconds() * 1000  # can exceed 2^31 for multi-week sessions`

and `schema.sql`:
> "The >int range values come from the historical importer (`import_sessions.py`)…"

The developer's justification ("The mobile SDK never sends more than 60 seconds") only covers one of two writers. The importer writes values up to ~4.2e9 and is still part of the schema's data contract.

### 2. Full table rewrite on a large, append-heavy table (OPERATIONAL RISK)

The migration has no `USING` clause, so PostgreSQL performs a default assignment cast and rewrites the entire table under `ACCESS EXCLUSIVE` lock.

- Table size: `schema.sql` → "Rows: **~48,000,000**"
- This blocks all reads and writes for the duration of the rewrite — directly impacts the Django REST API (rolling deploy) and the nightly 90-day retention delete.

### 3. Semantic mismatch with application

- `src/events/aggregate.py:19` computes p50/p95 with `avg()` over `duration_ms`; the importer's epoch-derived durations are deliberate, not noise.
- `dashboards/event_latency.sql:12` "casts `duration_ms` to `numeric` for division" — confirms the column is expected to carry values beyond a 60s SDK payload.

The proposed change does not match the actual data contract, only the mobile SDK's declared type.

### 4. Data not yet fully characterized

`schema.sql` proves 3,842 out-of-range rows exist **today**, but retention only prunes rows older than 90 days. Any importer backfill or SDK bug that writes large durations after the migration would also fail at write time once the column is `integer` — a runtime failure, not just a migration failure.

---

## Severity rationale

**HIGH**, not **BLOCKER**: PostgreSQL validates before writing, so a failed statement leaves no corrupted data (recovery is possible). **HIGH**, not **LOW**: the migration does not merely make the column smaller — it is guaranteed to fail on current data and would rewrite 48M rows. Classifying it `LOW` because "integer is smaller" is incorrect.

---

## Required changes before merge

1. **Do not narrow the column.** Keep `bigint`. If the mobile SDK needs an integer contract, enforce it in the SDK or at the API boundary, not in the shared column.
2. **If narrowing is still required**, it cannot proceed without:
   - Confirming the importer's max value (`4.2e9`) is acceptable to truncate/convert — **it is not**, based on the data facts.
   - Handling the **3,842 existing out-of-range rows** (e.g. clamp with `LEAST(duration_ms, 2147483647)` via `USING`, after explicit product approval to lose precision).
   - A `USING duration_ms::integer` clause (still fails on the 3,842 rows unless pre-cleaned).
3. **If a rewrite is unavoidable**, use an online pattern: add a new nullable `integer` column, backfill in batches, swap via a view or after a double-write period — not a single blocking `ALTER`.
4. **Re-run the survey** immediately before the migration on a fresh snapshot; the 3,842 count is from 2026-11-16 and may have grown.

**Recommendation:** Reject this migration. The correct change is to keep `duration_ms` as `bigint` and constrain it at the write boundary instead.