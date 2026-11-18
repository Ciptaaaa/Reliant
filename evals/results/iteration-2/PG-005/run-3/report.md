# Migration Guard Report — PG-005

## Migration under review

`db/migrations/0088_events_duration_ms_int.sql`:
```sql
ALTER TABLE events ALTER COLUMN duration_ms TYPE integer;
```

## Findings

### 1. Values exceed the target type — migration will fail (blocking execution)

**Evidence (schema.sql):**
```
SELECT max(duration_ms), min(duration_ms) FROM events;         -> 4212849000, 0
SELECT count(*) FROM events WHERE duration_ms > 2147483647;     -> 3,842
```

**Evidence (application code):**
```python
duration = (ended_at - started_at).total_seconds() * 1000  # can exceed 2^31 for multi-week sessions
```

PostgreSQL cannot represent `4,212,849,000` in `integer` (max `2,147,483,647`). With no `USING` clause, the implicit `bigint → integer` cast has no lossy assignment cast defined, so `ALTER COLUMN ... TYPE integer` will **abort with a "value out of range" / "cannot be cast automatically" error**. These values are *live* right now (3,842 rows), not hypothetical — the column is populated, so the statement fails on this data set.

Even if it currently succeeded (e.g. against a snapshot with no large values), the historical importer at `import_sessions.py:37` continues to write values up to ~4.2e9, so a later re-run or a fresh DB would fail again. There is also a latent **correctness** risk: if a future run used an explicit `USING duration_ms::integer`, PostgreSQL's integer cast would raise on those 3,842 rows (not truncate), so the failure mode is a hard error, not silent corruption.

> The case note is correct: `bigint → integer` is a *narrowing* change; PostgreSQL **aborts instead of truncating**. This is a check, not silent corruption.

### 2. Table rewrite on 48M rows — operational risk

The type change requires a full table rewrite of `events` (~48,000,000 rows). During the rewrite, PostgreSQL takes an `ACCESS EXCLUSIVE` lock that blocks **all** reads and writes (including the mobile SDK ingest at `ingest.py:52` and the API serializer at `serializers.py:31`) for the duration. On an append-heavy log that is a production outage window and will queue writes.

### 3. Application contract — SDK only sends ≤60 s, but importer disagrees

The developer's justification ("The mobile SDK never sends more than 60 seconds, so integer is plenty") is only half the picture. **Evidence (application-usage.md):**
- `ingest.py:52` — mobile SDK, `int`, 0–60,000. ✔ fits `integer`
- `import_sessions.py:37` — historical importer, up to ~4.2e9. ✘ does not fit

The column is shared by two writers, so the narrow type is invalid for the stated workload. The "SDK is separate repo, not searched" note does not change this — the importer *in this repo* already proves the column exceeds `int` range.

### 4. Reads that would be affected

- `serializers.py:31` returns `duration_ms` as an integer in JSON — a `bigint` returned as `int` is fine up to JS safe integer limits; the issue is the write side, not this read.
- `dashboards/event_latency.sql:12` casts to `numeric` for division — unaffected by the column type.

## Verdict

**REJECT — do not merge.** The migration will fail on current data (3,842 rows exceed `integer` max) and, when it fails, will abort the ALTER rather than convert. Additionally, the 48M-row table rewrite under `ACCESS EXCLUSIVE` lock is a production availability risk. The stated rationale is contradicted by the in-repo importer.

## Recommended remediation

Choose one of:

1. **Keep `bigint`.** The values genuinely exceed `integer` range; the "SDK declares int" argument is not a sufficient reason to narrow a column shared with the importer. No migration needed.
2. **If the goal is type alignment only:** add a `CHECK (duration_ms <= 2147483647)` (as `NOT VALID` then validate, to avoid blocking) is the wrong direction — it would reject importer rows.
3. **If you truly must move off `bigint`:** first fix `import_sessions.py:37` to clamp/convert epoch-derived durations to a sane unit (e.g. seconds or capped ms) and backfill the 3,842+ offending rows, *then* migrate with an explicit `USING duration_ms::integer` and a pre-flight audit query. Also do the rewrite via a new column + backfill in batches + swap, to avoid a long `ACCESS EXCLUSIVE` lock.

Any remediation must treat the 3,842 current >int rows and the importer as first-class constraints, not edge cases.

**Severity: HIGH** (per case guidance — operational risk + guaranteed migration failure, recovery possible, no silent data destruction).