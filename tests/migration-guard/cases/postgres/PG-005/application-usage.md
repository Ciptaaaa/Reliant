# Application usage: `events.duration_ms`

## Writes

- `src/events/ingest.py:52` — mobile SDK payload, `duration_ms` is an `int`; observed range 0–60,000.
- `src/events/import_sessions.py:37`
  ```python
  duration = (ended_at - started_at).total_seconds() * 1000  # can exceed 2^31 for multi-week sessions
  ```
  → historical importer, writes values up to ~4.2e9.
- `src/events/aggregate.py:19` — computes p50/p95 over `duration_ms` with `avg()` in SQL.

## Reads

- `src/api/events/serializers.py:31` — returns `duration_ms` as an integer in JSON.
- Dashboard query `dashboards/event_latency.sql:12` casts `duration_ms` to `numeric` for division.

## Not searched

- The mobile SDK repository (separate).
