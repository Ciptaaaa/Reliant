# MY-002 — `NOT NULL` on a column that still has `NULL` rows

## Context

- **Engine**: MySQL 8.0.36, InnoDB, single primary with one async read replica
- **Application**: Laravel 11 queue worker + HTTP API, rolling deploy
- **What the developer said**:
  > `payments.method` is always set by the checkout flow, so I want it `NOT NULL`. The column is used
  > in reporting only. Migration:
  > `ALTER TABLE payments MODIFY COLUMN method VARCHAR(24) NOT NULL;`
- **Table**: `payments`, ~11M rows; old rows imported from a previous gateway.

## Deployment facts

- No `ALGORITHM=` / `LOCK=` clause in the migration.
- Replica reads: reporting queries run on the replica and lag during expensive DDL.
- MySQL DDL implicitly commits.

## Why this case exists

On MySQL there is no PostgreSQL-style `NOT VALID`/`VALIDATE` for `NOT NULL`: adding the constraint
validates existing rows at that moment, and it fails if any `NULL` exists. `MODIFY COLUMN` without an
explicit `ALGORITHM` may fall back to a copy/rebuild path. The agent must find the `NULL` rows, the
deploy failure, and the metadata-lock/rebuild cost, and recommend a batched backfill plus an explicit
`ALGORITHM=INPLACE, LOCK=SHARED` (or a maintenance window) rather than claiming the operation is
instant.
