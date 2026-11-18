# PG-007 — Add a nullable column

## Context

- **Engine**: PostgreSQL 15
- **Application**: Rails 7 API, rolling deploy
- **What the developer said**:
  > We want to let users set a preferred language. Adding a nullable column:
  > `ALTER TABLE users ADD COLUMN locale varchar(10);`
  > It is optional, so existing rows stay `NULL`. Fine to deploy during business hours?
- **Table**: `users`, ~310k rows, moderate traffic.

## Deployment facts

- Application writes will start using `locale` in a later release; this migration does not touch
  existing columns.
- No default value.

## Why this case exists

The correct answer is **APPROVED** with a `LOW` finding. This case checks that the agent does not
invent risk. On PostgreSQL 11+, adding a nullable column with no default is metadata-only: no table
rewrite, sub-millisecond, and the `ACCESS EXCLUSIVE` lock is held only for the catalog update. A
report that calls this `MEDIUM` or says it rewrites the table is a false positive.
