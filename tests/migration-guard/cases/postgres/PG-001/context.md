# PG-001 — Drop a column that application code still uses

## Context

- **Engine**: PostgreSQL 15 (set in `docker-compose.yml`, Postgres runs as a managed single primary)
- **Application**: Rails 7 monolith, deployed with a rolling deploy (2 replicas, zero downtime)
- **What the developer said**:
  > We replaced the phone login with email+password last quarter. I want to drop `users.phone` now
  > because it is unused and it is PII we no longer need to store. Migration ends up as
  > `ALTER TABLE users DROP COLUMN phone;`. Safe?
- **Table**: `users`, reported by the developer as "a few hundred thousand rows".

## Deployment facts

- Rolling deploy: old and new application versions run simultaneously during the deploy window.
- The release containing the "phone removed" code has **not been deployed yet**. `main` still contains
  the phone code; the migration branch is separate.
- Nightly `pg_dump` to object storage, 24h RPO. Restores are performed roughly once a quarter.

## Why this case exists

Tests whether the agent traces application usage instead of accepting "it is unused" from the user,
and whether it separates data loss from schema rollback.
