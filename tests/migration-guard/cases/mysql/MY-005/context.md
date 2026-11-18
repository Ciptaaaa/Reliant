# MY-005 — Online DDL with an algorithm the operation does not support

## Context

- **Engine**: MySQL 8.0.36, InnoDB
- **Application**: Laravel 11, rolling deploy
- **What the developer said**:
  > `invoices.currency` is `CHAR(3)` and we want it `VARCHAR(3)` for consistency. I want to avoid the
  > table copy, so I added the online flags:
  > `ALTER TABLE invoices MODIFY COLUMN currency VARCHAR(3) NOT NULL, ALGORITHM=INSTANT, LOCK=NONE;`
  > It is a metadata change, it should be instant.
- **Table**: `invoices`, ~18M rows; hot write path (every order creates an invoice).

## Deployment facts

- No staging test of this specific ALTER has been run.
- The deploy runs migrations as a single step before starting new tasks.

## Why this case exists

`ALGORITHM=INSTANT` is only supported for a specific and evolving set of operations, and a column type
change is not one of them. When the requested algorithm is not supported, MySQL **rejects the
statement** — which is safe, but means the deploy fails. The agent must state that INSTANT does not
apply here, that the fallback is a rebuild/copy with the associated locking, and must not claim the
operation is instant because the keyword is present. It must also avoid the opposite error: claiming
the table will be silently copied (it will not — the statement errors out).
