# PG-005 — Type conversion that truncates values

## Context

- **Engine**: PostgreSQL 15
- **Application**: A Django REST API, rolling deploy
- **What the developer said**:
  > We want to move `events.duration_ms` from `bigint` to `integer` to match the mobile SDK's declared
  > type. The mobile SDK never sends more than 60 seconds, so integer is plenty. Migration:
  > `ALTER TABLE events ALTER COLUMN duration_ms TYPE integer;`
- **Table**: `events` ~48M rows (an append-heavy event log), retention 90 days.

## Deployment facts

- `duration_ms` is written by the mobile SDK (values normally < 60,000) **and** by a batch importer
  that loads historical sessions with values up to ~4.2 billion (epoch-derived).
- The migration has no `USING` clause.
- The type change rewrites the table.

## Why this case exists

`bigint → integer` is a narrowing change: values above `2147483647` cannot be represented, and
PostgreSQL aborts instead of truncating (a check, not a silent corruption) — but the migration still
fails, and the table rewrite on 48M rows is a large operational risk. The correct severity is `HIGH`
(risk to data and operations, with recovery possible), not `BLOCKER` (no data is destroyed if the
statement fails). A report that classifies it `LOW` because "integer is smaller" is wrong.
