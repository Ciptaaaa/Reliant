# Migration Guard Report — PG-005

**Migration:** `db/migrations/0088_events_duration_ms_int.sql`
**Verdict:** ❌ **DO NOT MERGE (HIGH severity)**
**One-line summary:** A narrowing `bigint → integer` conversion on a 48M-row table that (a) will fail outright because ~3,842 existing rows exceed `int` range, and (b) forces a full table rewrite — but no data is destroyed if the statement fails.

---

## Findings

### 1. Narrowing conversion will fail on existing data — HIGH
**Evidence.** The migration is:
```sql
ALTER TABLE events ALTER COLUMN duration_ms TYPE integer;
```
`bigint → integer` is narrowing; `integer` max is `2147483647`. The developer's own data check on the live table:
```
-- Data facts (developer ran on 2026-11-16):
--   SELECT max(duration_ms), min(duration_ms) FROM events;  -> 4212849000, 0
--   SELECT count(*) FROM events WHERE duration_ms > 2147483647; -> 3,842
```
**Effect.** There are **3,842 rows** (max **4,212,849,000**) that cannot be represented as `integer`. PostgreSQL will **abort the `ALTER`** with an out-of-range error. The migration will not succeed as written. This is a hard, deterministic failure — not a race or edge case.

**Root cause is in the application, not just the data.** `src/events/import_sessions.py:37`:
```python
duration = (ended_at - started_at).total_seconds() * 1000  # can exceed 2^31 for multi-week sessions
```
→ writes values up to ~4.2e9. So even if you clean existing rows, **the importer will immediately re-generate out-of-range values** and future writes will fail (or, if the column were already integer, the importer's insert would error / the value would be rejected).

### 2. Full table rewrite on 48M rows — HIGH (operational)
**Evidence.** `context.md`: "The type change rewrites the table." Table: `events ~48M rows (an append-heavy event log), retention 90 days.` Deployment: "A Django REST API, **rolling deploy**."
**Effect.** `ALTER COLUMN ... TYPE` on PG15 rewrites the entire table and takes an `ACCESS EXCLUSIVE` lock for the duration. On 48M rows this is a long lock → write/read blocking, lock queue buildup, and bloat (old table copy retained). With a rolling deploy, old app versions still write `bigint`-range values during the migration window. This is a large operational risk independent of the failure above.

### 3. Application contract mismatch — HIGH
**Evidence.** Writers/readers assume `bigint` semantics:
- `src/events/import_sessions.py:37` — writes up to ~4.2e9.
- `src/events/aggregate.py:19` — `avg()` in SQL over `duration_ms` (fine numerically, but will fail to insert over-range inputs).
- `src/api/events/serializers.py:31` — returns `duration_ms` as integer in JSON (JSON has no issue; the concern is the DB rejecting writes).
- `dashboards/event_latency.sql:12` — casts to `numeric` for division (must handle potential overflow before cast; with integer source, fine, but the importer can't store its values).

The stated rationale — *"The mobile SDK never sends more than 60 seconds, so integer is plenty"* — is **incomplete**. It ignores the **historical importer**, which is the source of the 4.2e9 values. The "Not searched" note admits the mobile SDK repo was not checked, but the decisive counter-evidence is in-repo (`import_sessions.py`).

### 4. No `USING` clause / silent-truncation check
**Evidence.** `context.md`: "The migration has no `USING` clause." PostgreSQL **aborts** on out-of-range rather than truncating — so this is a **safe failure**, not silent corruption. Correct severity is therefore **HIGH**, not **BLOCKER** (recovery possible; no data destroyed on failure). This matches the case's own guidance.

---

## Severity classification

| Check | Result |
|---|---|
| Data destroyed on success? | No silent truncation — PG aborts. |
| Migration will succeed? | **No** — 3,842 rows out of range. |
| Operational lock/rewrite risk? | **Yes** — 48M-row rewrite, `ACCESS EXCLUSIVE`, rolling deploy. |
| App compatibility? | **No** — importer writes >int. |
| **Overall** | **HIGH** — data + operational risk, recoverable |

**Not** `LOW` ("integer is smaller" is not a safety argument) and **not** `BLOCKER` (failure mode is a clean abort, not data loss).

---

## Required before merge

1. **Resolve the range conflict.** Either keep `bigint`, or change the importer (`import_sessions.py:37`) to clamp/reject/partition values `> 2147483647` and re-verify with `SELECT count(*) ... WHERE duration_ms > 2147483647` → must be 0.
2. **Never run this as a single `ALTER` on live 48M rows.** Use the expand/contract pattern:
   - Add `duration_ms_int integer` (nullable), backfill in batches, dual-write, switch reads, then drop old column.
   - Or accept a maintenance window with explicit lock/rewrite budget.
3. **No `USING` truncation.** If a narrowing conversion is ever kept, add an explicit `USING` with a guard (e.g., `CASE WHEN duration_ms > 2147483647 THEN NULL ELSE duration_ms::integer END` or a pre-check) and document that overflow rows are handled — never rely on implicit behavior.
4. **Account for rolling deploy.** Old app versions writing `bigint` values during the window will fail once the column is narrowed; gate the deploy on the dual-write/backfill completing.

---

## Verdict

**DO NOT MERGE.** `0088_events_duration_ms_int.sql` as written **will fail** (`4212849000` > `2147483647` across 3,842 rows), is sourced by an importer that **keeps producing over-range values**, and triggers a **full 48M-row rewrite with `ACCESS EXCLUSIVE` locking** during a rolling deploy.

> Evidence: `max(duration_ms) -> 4212849000`; `count(*) WHERE duration_ms > 2147483647 -> 3,842`; `import_sessions.py:37` "can exceed 2^31"; "The type change rewrites the table."

**Classification: HIGH.** Recoverable (statement aborts cleanly, no truncation), but unacceptable as a migration and incompatible with the application's write path.