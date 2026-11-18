# MY-008 — Rename a column with an application-compatibility problem

## Context

- **Engine**: MySQL 8.0.36, InnoDB
- **Application**: Laravel 11, ECS rolling update (2 tasks), plus a nightly cron task that runs the
  last released image
- **What the developer said**:
  > `customers.mobile` should be `customers.mobile_phone`. I renamed it everywhere in the code and added:
  > `ALTER TABLE customers RENAME COLUMN mobile TO mobile_phone, ALGORITHM=INSTANT;`
  > The rename is instant, so no downtime.
- **Table**: `customers`, ~620k rows.

## Deployment facts

- The migration runs before the new application tasks start.
- Old tasks continue serving for several minutes during the rolling update.
- The nightly cron image is built from the last release.

## Why this case exists

MySQL's `RENAME COLUMN` (8.0.28+; `CHANGE COLUMN` on older versions) is instant for the engine, but
breaks code that reads the old name. The classic MySQL trap is using `CHANGE COLUMN old new <full
definition>` and accidentally changing the type or losing attributes; the agent should verify the
version specific behaviour and not conflate "instant DDL" with "safe for a rolling deploy".
