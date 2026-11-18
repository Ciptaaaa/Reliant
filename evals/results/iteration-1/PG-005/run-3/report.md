# Migration Guard Report — PG-005

**Migration:** `db/migrations/0088_events_duration_ms_int.sql`
`ALTER TABLE events ALTER COLUMN duration_ms TYPE integer;`

## Verdict

> **HIGH — Do not merge as written.**
> This is a narrowing `bigint → integer` conversion on a 48M-row table that contains out-of-range values today. The statement will fail (abort, not silently truncate) — but only after triggering a full-table rewrite. Data is recoverable and nothing is corrupted, so it is not `BLOCKER`; it is `HIGH` (data + operational risk).

---

## Findings

### 1. The migration will fail on existing data — HIGH

Evidence from `schema.sql` (developer's own query, 2026-11-16):

```
SELECT max(duration_ms), min(duration_ms) FROM events;    -> 4212849000, 0
SELECT count(*) FROM events WHERE duration_ms > 2147483647; -> 3,842
```

- `max(duration_ms) = 4,212,849,000` is **>** `integer` max (`2,147,483,647`).
- **3,842 rows** currently exceed the target type's range.

PostgreSQL does not truncate here — the `ALTER` will raise
`ERROR: integer out of range` / `value "4212849000" is out of range for type integer` and roll back.
So the migration **cannot succeed against production as written**, regardless of runtime. The developer's justification ("the mobile SDK never sends more than 60 seconds") only covers one of two writers.

### 2. A second writer legitimately produces out-of-range values — HIGH

`application-usage.md`, `src/events/import_sessions.py:37`:

```python
duration = (ended_at - started_at).total_seconds() * 1000  # can exceed 2^31 for multi-week sessions
```

> → historical importer, writes values up to ~4.2e9.

This is an active code path, not dead data. Narrowing the column would make the importer fail on future multi-week sessions **even if** existing rows were cleaned. The type change is incompatible with a documented writer.

### 3. Table rewrite on 48M rows — HIGH (operational)

- `ALTER COLUMN ... TYPE` from `bigint` to `integer` is **not** a binary-coercible, in-place change; it requires a **full-table rewrite** with `ACCESS EXCLUSIVE` lock.
- Table size: **~48,000,000 rows** (append-heavy event log).
- During the rewrite the table is locked → writes from the mobile SDK and the importer block; API reads via `events_session_id_idx` depending on lock acquisition are affected. On a rolling deploy this is a hard outage window proportional to table size.
- If the rewrite is attempted and then aborts on the out-of-range check (§1), you incur the full rewrite cost and the lock for nothing.

### 4. No `USING` clause — informational, but moot

The `context.md` notes the migration has no `USING` clause. A `USING duration_ms::integer` cast would **also** fail on the same out-of-range rows (casts are checked), so adding `USING` does not fix §1 — it only changes the error path. The core problem is the target type is too narrow for the data.

### 5. Downstream consistency check — LOW

- `src/events/aggregate.py:19` (`avg()` over `duration_ms`) and `dashboards/event_latency.sql:12` (`duration_ms::numeric` division) work identically on `integer` or `bigint`. No breakage.
- `src/api/events/serializers.py:31` returns `duration_ms` as JSON integer — fine either way.
- **Not searched:** the mobile SDK repository (separate). Per the skill, un-searched sources are a *residual* risk, not a finding — but note it means the claim "SDK never exceeds 60s" is unverified here.

---

## Recommendation

1. **Do not merge `0088` as written.** It fails on prod data and rewrites 48M rows under an exclusive lock.
2. If the goal is "match the SDK's declared type," reconcile the **two-writer conflict first**:
   - Either change the importer to cap/normalize multi-week sessions (and backfill the 3,842 out-of-range rows),
   - Or accept that `duration_ms` must remain `bigint` and leave the schema alone (the `integer` narrowing buys essentially nothing on a `bigint`-keyed append table).
3. If a narrowing is still desired after data cleanup, the safe sequence is:
   - Backfill/remediate the 3,842 rows,
   - Make the importer no longer produce values > 2^31,
   - Verify `SELECT count(*) FROM events WHERE duration_ms > 2147483647 = 0`,
   - Then run a **non-blocking** conversion (e.g., add a new `integer` column, backfill, swap) rather than an in-place `ACCESS EXCLUSIVE` rewrite on 48M rows.
4. Sign-off checklist: re-run the max/count queries post-remediation and confirm `0` before merging any narrowing migration.

---

**Severity justification (per skill):** Not `LOW` — "integer is smaller" ignores that production contains out-of-range values and an active writer produces them. Not `BLOCKER` — PostgreSQL aborts the statement; no data is destroyed and recovery is straightforward. The correct grade is **`HIGH`**: the migration is guaranteed to fail in production and, if attempted, imposes a large operational (lock/rewrite) cost on a 48M-row table.