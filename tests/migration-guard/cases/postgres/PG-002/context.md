# PG-002 — `SET NOT NULL` while `NULL` rows exist

## Context

- **Engine**: PostgreSQL 15
- **Application**: Node/Express API, rolling deploy
- **What the developer said**:
  > We want `orders.status` to be required. I added `ALTER TABLE orders ALTER COLUMN status SET NOT NULL;`.
  > The column already has values for all new orders. Can I ship this?
- **Table**: `orders`. The developer says it has "several million rows" and receives about 400
  inserts per minute at peak.

## Deployment facts

- No maintenance window. Migration runs automatically as part of the deploy.
- ORM: Prisma; the migration file is applied inside a single transaction by the deploy step.

## Why this case exists

Tests whether the agent (a) sees the `NULL` rows in the schema/notes rather than trusting "it already
has values", (b) knows that `SET NOT NULL` takes `ACCESS EXCLUSIVE` and scans the whole table, and
(c) recommends the `CHECK … NOT VALID` → `VALIDATE` → `SET NOT NULL` sequence instead of a backfill
alone.
