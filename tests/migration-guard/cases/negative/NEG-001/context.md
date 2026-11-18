# NEG-001 — Add a nullable column, touch nothing else

## Context

- **Engine**: PostgreSQL 16
- **Application**: Rails 7 API, rolling deploy
- **What the developer said**:
  > We need to store an optional "how did you hear about us" answer on signups. Adding a nullable
  > column: `ALTER TABLE signups ADD COLUMN referral_source varchar(64);`
  > Nothing else changes in this migration.
- **Table**: `signups`, ~90k rows.

## Deployment facts

- No default, no constraint, no index, no backfill in this migration.
- No existing query modified.

## Why this case exists (negative)

This must be **APPROVED** with a `LOW` finding. Any report that flags a data-integrity risk, claims a
rewrite, or recommends a backfill is a false positive. The case calibrates the agent's restraint.
