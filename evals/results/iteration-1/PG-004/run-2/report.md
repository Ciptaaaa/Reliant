# Migration Guard Report — PG-004

**Migration:** `migrations/2026_11_16_add_orders_customer_fk.sql`
**Target:** PostgreSQL 14, `orders` (~7M rows) ↔ `customers` (~180k rows), PHP/Symfony rolling deploy, migrations wrapped in a single transaction.

## Verdict: **BLOCKED — do not merge**

The proposed statement will fail at execution, and even after the data is fixed it will break an existing feature. Two independent defects must both be addressed.

---

## Finding 1 — BLOCKER: orphan rows violate the FK; the migration will not apply

The migration:

```sql
ALTER TABLE orders
  ADD CONSTRAINT orders_customer_fk
  FOREIGN KEY (customer_id) REFERENCES customers (id);
```

PostgreSQL validates all existing rows when adding a `FOREIGN KEY` (unless `NOT VALID` is specified). The developer's own data check shows this will fail:

> ```
> SELECT count(*) FROM orders o
>   LEFT JOIN customers c ON c.id = o.customer_id
>  WHERE o.customer_id IS NOT NULL AND c.id IS NULL;  -> 22,913  -- orphans
> ```

> "Orphans come from a 2022 data import where the customer table was rebuilt and IDs changed."

- `customer_id IS NULL` count is `0`, so these are genuine dangling references, not the usual NULL pass-through.
- Default `MATCH SIMPLE` lets `NULL` through, but not a non-null value with no parent.
- Expected outcome: `ERROR: insert or update on table "orders" violates foreign key constraint "orders_customer_fk"`, with the conflicting rows in the detail. Because the deploy pipeline wraps migrations in a single transaction, the whole migration rolls back and the deploy fails.

**Required before merge:** decide where the 22,913 orphans go, and do the cleanup in its own, idempotent, separately reviewed migration. For example, set them to `NULL` (the application already tolerates a `NULL` join: "the UI tolerates `NULL` joins", `OrderRepository.php:34`), and fix `LegacyOrderImport.php:61`, which "inserts orders … without checking that the customer exists." That importer can recreate orphans after cleanup. Then verify `count(*) = 0` before shipping the constraint.

---

## Finding 2 — HIGH: default `NO ACTION` breaks the hard-delete endpoint

The proposed FK has **no `ON DELETE` clause**, so the behaviour is `NO ACTION`.

The developer's claim is:

> "The customer delete endpoint hard-deletes customers, but orders are supposed to cascade. Should be fine."

The `CASCADE` action does not exist yet. The evidence shows the opposite:

- `AdminCustomerController.php:120` calls `remove($customer); flush();`, which produces a plain `DELETE FROM customers WHERE id = ?`.
- No migration defines `ON DELETE` on any related table.

Once the FK is live, `DELETE FROM customers` with dependent orders raises `SQLSTATE[23503]`. The application-usage notes predict this exactly:

> "With an FK using the default `NO ACTION`, this endpoint starts throwing `SQLSTATE[23503]` … for every existing customer that has orders — which is almost all of them."

**Pick an explicit action and ship it in this migration (or a companion one):**

- `ON DELETE SET NULL` matches the only documented delete semantics. The UI tolerates `NULL` customers, and the order history must survive (`anonymise` already prefers to keep rows and strip PII rather than delete them). This is the recommended option.
- `ON DELETE CASCADE` would silently destroy ~7M rows of order/financial history. Do not do this without a product decision, and the developer's "supposed to cascade" is not one.
- `ON DELETE RESTRICT` is honest but makes the admin endpoint permanently fail. If that is intended, the endpoint must be reworked (soft delete or anonymise) and the UI's error handling checked. That was **not searched**: "The admin UI's error handling for the delete endpoint."

---

## Rollout notes (secondary)

- **Write lock duration:** a plain `validated` `ADD CONSTRAINT` takes `SHARE ROW EXCLUSIVE` on `orders` and scans ~7M rows in one transaction, blocking writes to `orders` for the whole scan. The validate step's lock is light, but the initial ADD is not. Recommended sequence:
  1. Clean the orphan data (separate migration).
  2. `ADD CONSTRAINT ... FOREIGN KEY (...) REFERENCES customers (id) ON DELETE SET NULL NOT VALID;`
  3. `ALTER TABLE orders VALIDATE CONSTRAINT orders_customer_fk;` in a separate transaction.
- **Orphan rate during validation:** new orphans can be created by the legacy importer between `NOT VALID` and `VALIDATE`. Fix the importer first, or the `VALIDATE` will fail.
- **Existing index:** `orders_customer_id_idx` already exists, so no extra parent-side lookup cost is needed for the FK.

---

## Summary

| # | Severity | Issue | Evidence |
|---|----------|-------|----------|
| 1 | **BLOCKER** | 22,913 orphan `orders.customer_id` rows make the FK validation fail | `schema.sql` data facts, orphans from 2022 import |
| 2 | **HIGH** | Default `NO ACTION` breaks `AdminCustomerController::120` hard delete | `application-usage.md` deletion path; no `ON DELETE` anywhere |

**Action:** Do not merge as written. Clean data, fix the importer, declare an explicit `ON DELETE` action (recommend `SET NULL`, matching the tolerant UI), and use the `NOT VALID` → `VALIDATE` two-step to limit locking. A report that treats `NOT VALID` as a sufficient fix has missed Finding 1: `NOT VALID` only moves the failure from deploy time to the `VALIDATE` step.