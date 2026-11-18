# NEG-002 — Create an index that the code actually uses

## Context

- **Engine**: PostgreSQL 16
- **Application**: Django 5 API, rolling deploy
- **What the developer said**:
  > The order history page filters by `customer_id` and `placed_at`. There is only an index on
  > `customer_id`. I want to add a composite index:
  > `CREATE INDEX CONCURRENTLY orders_customer_placed_idx ON orders (customer_id, placed_at DESC);`
  > The migration is marked non-transactional in the runner.
- **Table**: `orders`, ~9M rows.

## Deployment facts

- The migration runner supports `atomic = False` and it is set for this migration (verified in the
  migration file header).
- No `DROP INDEX`, no constraint change.

## Why this case exists (negative)

A correctly written `CONCURRENTLY` index on a used column is the model of a safe migration. The correct
answer is **APPROVED** with a `LOW` finding (long but non-blocking build, invalid-index check as a
precaution). A report that flags write blocking, a missing transaction, or an unsafe `DROP` is a false
positive.
