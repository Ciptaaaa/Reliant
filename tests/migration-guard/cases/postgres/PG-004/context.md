# PG-004 — `FOREIGN KEY` with orphan rows

## Context

- **Engine**: PostgreSQL 14
- **Application**: PHP/Symfony monolith, rolling deploy
- **What the developer said**:
  > We added `orders.customer_id` last year but never created the FK. Adding it now:
  > `ALTER TABLE orders ADD CONSTRAINT orders_customer_fk FOREIGN KEY (customer_id) REFERENCES customers (id);`
  > The customer delete endpoint hard-deletes customers, but orders are supposed to cascade. Should be fine.
- **Table**: `orders` ~7M rows, `customers` ~180k rows.

## Deployment facts

- Customer records are hard-deleted by an admin endpoint with no cascade to `orders`.
- There is no `ON DELETE` clause in the proposed FK, so the default is `NO ACTION`.
- The deploy pipeline wraps all migrations in a single transaction.

## Why this case exists

Two things must be found: (a) orphan `customer_id` values make the FK fail (`BLOCKER`), and (b) even
with clean data, the default `NO ACTION` will break the existing hard-delete endpoint (`HIGH`
compatibility risk). A report that only says "use `NOT VALID` and it is fine" has skipped the orphan
rows.
