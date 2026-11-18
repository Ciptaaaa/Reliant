-- db/migrations/0088_events_duration_ms_int.sql
ALTER TABLE events ALTER COLUMN duration_ms TYPE integer;
