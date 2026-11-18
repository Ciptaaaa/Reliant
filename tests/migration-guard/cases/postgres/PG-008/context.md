# PG-008 — Rename a column while the old app version is still running

## Context

- **Engine**: PostgreSQL 15
- **Application**: Rails 7 monolith, 3 replicas, zero-downtime rolling deploy
- **What the developer said**:
  > `users.phone` should be `users.phone_number` for consistency with the CRM. I renamed it in code and
  > added:
  > `ALTER TABLE users RENAME COLUMN phone TO phone_number;`
  > The rename is instant in PostgreSQL, so it should be safe, right?
- **Table**: `users`, ~310k rows.

## Deployment facts

- Rolling deploy: for the duration of the deploy, old code (reading `phone`) and new code (reading
  `phone_number`) run against the same schema.
- The migration runs **before** the new application pods start.
- A background export job reads `users.phone` (`lib/tasks/export_support.rake`), continuously every
  night.
- `application-usage.md` lists the current references to `phone`.

## Why this case exists

This is the "instant but breaking" case. A rename takes `ACCESS EXCLUSIVE` but completes immediately,
so lock analysis alone says "safe". The actual risk is deploy-order incompatibility: after the rename
and before the new pods are live, old pods raise `column "phone" does not exist` on every request that
touches it. The agent must reason about the rolling deploy, not only about the DDL cost.
