# MY-004 — `FOREIGN KEY` with orphan rows

## Context

- **Engine**: MySQL 8.0.36, InnoDB
- **Application**: Laravel 11, rolling deploy
- **What the developer said**:
  > `payments.order_id` was added before we turned on foreign keys. Now we want the FK:
  > `ALTER TABLE payments ADD CONSTRAINT payments_order_id_fk FOREIGN KEY (order_id) REFERENCES orders (id);`
  > Orders are hard-deleted by the admin panel with a cascade, so this should be consistent.
- **Table**: `payments` ~11M rows, `orders` ~3.2M rows.

## Deployment facts

- The admin panel currently deletes orders with `Order::destroy()` and **deletes payments first**
  manually in a service.
- There is no `ON DELETE` clause in the proposed FK.
- `orders.id` is `BIGINT UNSIGNED`.

## Why this case exists

MySQL validates FK data at `ADD CONSTRAINT` time (no `NOT VALID`), so orphans make it fail outright.
A second trap: an FK on a type-mismatched column (e.g. `INT` referencing `BIGINT UNSIGNED`) is rejected
outright — this fixture makes the column types compatible so the case tests the orphan discovery, not
a type error. The agent must also catch that the default `NO ACTION`/`RESTRICT` changes the delete
service's behaviour.
