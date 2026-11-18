# Migration Guard Report

## Scope
- **Migration**: `migrations/2026_11_16_add_orders_customer_fk.sql`
- **Engine**: PostgreSQL 14
- **Inspected**: `migration.sql`, `schema.sql`, `application-usage.md`, `context.md`
- **Not inspected**: the admin UI's error handling for the delete endpoint
- **Unknowns**: how often the admin delete endpoint is used on customers that have orders; the code path exists either way

## Verdict
**BLOCKED** — 22,913 orders reference a customer that does not exist, so the foreign key fails to build.

## Findings

### F1 — BLOCKER — foreign-key
- **Statement**: `ALTER TABLE orders ADD CONSTRAINT orders_customer_fk FOREIGN KEY (customer_id) REFERENCES customers (id)` in `migration.sql:2-4` — validates every existing `orders.customer_id`
- **Evidence**:
  - `schema.sql:20` — `SELECT count(*) ... customers c ON c.id = o.customer_id WHERE c.id IS NULL -> 22,913` orphans
  - `schema.sql:19` — "FK status: none exists on orders.customer_id", so the constraint validates all 7,004,118 rows
  - `migration.sql:2-4` — no `NOT VALID`; `context.md:16` says the pipeline wraps all migrations in a single transaction
- **Impact**: the statement aborts with `violates foreign key constraint`, the deploy transaction rolls back and no constraint is created; no data is changed because the statement is refused
- **Mitigation**: resolve or quarantine the 22,913 orphans first, or add the constraint `NOT VALID` so the build succeeds and repair the orphans before validating
- **Verification**: the orphan query returns 0; if `NOT VALID` was used, `SELECT convalidated FROM pg_constraint WHERE conname = 'orders_customer_fk'` returns true after `VALIDATE`
- **Approval**: none
- **Reversible**: yes

### F2 — HIGH — application-compatibility
- **Statement**: the constraint uses the default `NO ACTION`, which breaks the existing hard-delete endpoint the developer assumed would cascade
- **Evidence**:
  - `application-usage.md:29` — `src/Controller/AdminCustomerController.php:120` performs `$entityManager->remove($customer)`, a hard `DELETE FROM customers WHERE id = ?`
  - `application-usage.md:33` — "There is no cascade to `orders` and no `ON DELETE` behaviour implemented anywhere"
  - `migration.sql:2-4` — no `ON DELETE` clause; `context.md:15` confirms the default is `NO ACTION`
- **Impact**: every admin delete of a customer that has orders throws `SQLSTATE[23503] violates foreign key constraint`, so customer deletion stops working for almost all customers
- **Mitigation**: choose an explicit behaviour before adding the constraint — block deletion with a clear error, or soft-delete using the existing `customers.deleted_at`; do not rely on a cascade that no code implements
- **Verification**: deleting a test customer that has orders returns the intended result (a controlled error or a soft delete), not an unhandled 23503
- **Approval**: schedule
- **Reversible**: yes

### F3 — MEDIUM — lock-and-rewrite
- **Statement**: the validating build locks `orders` while it checks 7,004,118 rows, and the pipeline wraps it in a transaction that extends the lock
- **Evidence**:
  - `migration.sql:2-4` — no `NOT VALID`, so the build is fully validating
  - `schema.sql:17` — `orders` has 7,004,118 rows with `customer_id` indexed by `orders_customer_id_idx`
  - `context.md:16` — the pipeline wraps all migrations in a single transaction
- **Impact**: writes to `orders` block for the length of the validation, and the transaction wrapper holds the lock for every other statement in the same deploy
- **Mitigation**: add the constraint `NOT VALID` and validate it separately with `VALIDATE CONSTRAINT`, outside the deploy transaction
- **Verification**: `VALIDATE CONSTRAINT` completes while inserts on `orders` continue; `pg_stat_activity` shows no long `ACCESS EXCLUSIVE` wait
- **Approval**: schedule
- **Reversible**: yes

## Safer migration path
1. Decide the delete semantics: soft delete using `customers.deleted_at`, or a controlled block. Do not assume a cascade that no code implements.
2. Resolve the 22,913 orphans: map them to a real customer, create a placeholder for imported orders, or delete the orders after exporting them.
3. Add the constraint without a full validating lock:
   ```sql
   ALTER TABLE orders ADD CONSTRAINT orders_customer_fk FOREIGN KEY (customer_id) REFERENCES customers (id) NOT VALID;
   ```
4. Validate it in its own step, outside the deploy transaction: `ALTER TABLE orders VALIDATE CONSTRAINT orders_customer_fk;`
5. Update `AdminCustomerController.php:120` to the chosen delete behaviour, and deploy with or before the constraint.
6. Verify: the orphan query returns 0 and `convalidated` is true.

## Rollback and recovery
- **Schema rollback**: `ALTER TABLE orders DROP CONSTRAINT orders_customer_fk;` is immediate and clean.
- **Data recovery**: if step 2 repairs orphans by deleting or reassigning orders, export those rows first; that change is not reversible from the schema.
- **Application rollback**: safe only if the delete behaviour changed in the same release; otherwise deleting a customer fails again.

## Pre-deploy checklist
- [ ] Orphan count is 0, or `NOT VALID` is used with a dated repair plan
- [ ] Delete behaviour decided and implemented in `AdminCustomerController.php`
- [ ] Constraint added `NOT VALID` and validated outside the deploy transaction
- [ ] Orphan repairs exported before they are applied