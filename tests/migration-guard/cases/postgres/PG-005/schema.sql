-- Current schema (PostgreSQL 15)
-- Source: db/schema.sql @ 2026-11-10

CREATE TABLE events (
    id           bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    session_id   uuid          NOT NULL,
    event_type   varchar(64)   NOT NULL,
    duration_ms  bigint,
    payload      jsonb,
    occurred_at  timestamptz   NOT NULL
);

CREATE INDEX events_session_id_idx ON events (session_id);
-- 90-day retention: a job deletes rows older than 90 days nightly.
-- Rows: ~48,000,000.

-- Data facts (developer ran on 2026-11-16):
--   SELECT max(duration_ms), min(duration_ms) FROM events;         -> 4212849000, 0
--   SELECT count(*) FROM events WHERE duration_ms > 2147483647;     -> 3,842
--   The >int range values come from the historical importer (`import_sessions.py`),
--   which stores epoch-derived durations for sessions with a known start/end.
