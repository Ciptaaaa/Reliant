# MY-007 — Add a nullable column with instant DDL that is supported

## Context

- **Engine**: MySQL 8.0.36, InnoDB
- **Application**: Laravel 11, rolling deploy
- **What the developer said**:
  > We want an optional `nickname` on accounts. Adding:
  > `ALTER TABLE accounts ADD COLUMN nickname VARCHAR(80) DEFAULT NULL, ALGORITHM=INSTANT, LOCK=DEFAULT;`
  > I checked the docs: adding a column at the end with INSTANT is supported on 8.0. I want zero downtime.
- **Table**: `accounts`, ~2.4M rows.

## Deployment facts

- The new column is at the **end** of the table and has no default value (`DEFAULT NULL`).
- No application code reads or writes `nickname` yet.
- The deploy runs migrations before starting new tasks.

## Why this case exists

This is the MySQL mirror of PG-007, but with the extra requirement that the agent distinguish
"INSTANT was requested" from "INSTANT was actually applicable". The column is at the end and has no
non-null default, so `ALGORITHM=INSTANT` is genuinely supported on 8.0.12+. If the agent ignores the
version or claims a rebuild, it is a false positive. The correct answer is **APPROVED** with a `LOW`
finding.
