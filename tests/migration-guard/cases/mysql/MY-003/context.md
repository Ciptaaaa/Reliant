# MY-003 — `UNIQUE` index with duplicate rows

## Context

- **Engine**: MySQL 8.0.36, InnoDB
- **Application**: Laravel 11, rolling deploy
- **What the developer said**:
  > Customers can have the same `reference_code` because the generator had a bug for a week. I fixed
  > the generator. Now I want to prevent it with a unique index:
  > `ALTER TABLE customers ADD UNIQUE KEY customers_reference_code_unique (reference_code), ALGORITHM=INPLACE, LOCK=DEFAULT;`
  > Using online DDL so there is no downtime.
- **Table**: `customers`, ~620k rows.

## Deployment facts

- There is a cleanup script (`artisan customers:dedupe`) that runs manually; the last run was three
  weeks ago and is known to have left some duplicates.
- `reference_code` is uppercase alphanumeric, but one import path writes it lowercase.

## Why this case exists

The developer has done the right thing for locking (`INPLACE`, `LOCK=NONE`) and is still wrong: the
unique index cannot be built while duplicates exist, and `LOCK=NONE` may not even be permitted for a
unique index build. The agent must find the duplicate evidence and not be reassured by the online-DDL
clause.
