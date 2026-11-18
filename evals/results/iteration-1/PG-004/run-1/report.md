# Migration Guard Report — PG-004

**File reviewed:** `migrations/2026_11_16_add_orders_customer_fk.sql`

```sql
ALTER TABLE orders
  ADD CONSTRAINT orders_customer_fk
  FOREIGN KEY (customer_id) REFERENCES customers (id);
```

**Verdict: REJECT — do not merge as written.**

---

## Blockers

### BLOCKER 1 — Existing orphan rows cause the FK to fail

The migration will abort on data, not on syntax. Evidence from `schema.sql`:

> `SELECT count(*) FROM orders o LEFT JOIN customers c ON c.id = o.customer_id WHERE o.customer_id IS NOT NULL AND c.id IS NULL; -> 22,913` — orphans
>
> "Orphans come from a 2022 data import where the customer table was rebuilt and IDs changed."

`ALTER TABLE ... ADD CONSTRAINT ... FOREIGN KEY` validates every existing row. 22,913 rows reference non-existent `customers.id` values, so the statement raises:

```
ERROR: insert or update on table "orders" violates foreign key constraint "orders_customer_fk"
DETAIL: Key (customer_id)=(...) is not present in table "customers".
```

The migration cannot succeed until those rows are nulled, remapped, or the constraint is added `NOT VALID`. `NOT VALID` alone is not a fix — it only defers the problem and leaves the schema unenforced for new writes against bad existing data, and a later `VALIDATE CONSTRAINT` will fail on the same 22,913 rows.

---

## High-severity compatibility risk

### HIGH 1 — Default `ON DELETE NO ACTION` breaks the hard-delete endpoint

Even with clean data, the constraint changes runtime behaviour. From `application-usage.md`:

> `src/Controller/AdminCustomerController.php:120`
> ```php
> $entityManager->remove($customer);
> $entityManager->flush();
> ```
> → hard `DELETE FROM customers WHERE id = ?`. There is no cascade to `orders` and no `ON DELETE` behaviour implemented anywhere. With an FK using the default `NO ACTION`, this endpoint starts throwing `SQLSTATE[23503]` ... for every existing customer that has orders — which is almost all of them.

The developer's claim ("orders are supposed to cascade") is not implemented anywhere in code or schema; `orders.customer_id` has no `ON DELETE` and the app does not delete orders when a customer is deleted. As-written, the FK turns a working endpoint into a 500 for ~all customers.

### HIGH 2 — Application writes assume the column is unconstrained

- `src/Import/LegacyOrderImport.php:61` — "inserts orders with `customer_id` from a CSV **without** checking that the customer exists."
  After the FK is live, these imports start failing at insert time with `23503`. The import path was not designed for a constraint.

(Not yet assessed: the admin UI's handling of the delete endpoint's error — explicitly listed under "Not searched".)

---

## Locking / operational notes (informational)

- `orders` is ~7M rows; `ALTER TABLE ... ADD CONSTRAINT` takes `SHARE ROW EXCLUSIVE` and runs a full validation scan. Under the pipeline's single-transaction deploy, this blocks writes to `orders` for the duration.
- Customer count ~180k makes the FK index on `customers.id` (already the PK) sufficient for the reference side; the existing `orders_customer_id_idx` covers the child side, so no new index is required.

---

## Recommended path (not a merge approval)

1. Resolve orphans first (null them, or remap via the 2022 import mapping), separately, with its own verification query.
2. Add the constraint `NOT VALID`, then `VALIDATE CONSTRAINT` in a later migration once code is ready.
3. Decide and implement the delete semantics explicitly: `ON DELETE SET NULL` (order without customer already renders fine — see `OrderRepository.php:34` "UI tolerates `NULL` joins") or change the endpoint to soft-delete / anonymise (see `AnonymiseCustomersCommand.php`). Do **not** rely on cascade that doesn't exist.
4. Add existence validation to `LegacyOrderImport.php` before enabling enforcement.

---

**Verdict: REJECT.** The statement fails on 22,913 orphan rows (BLOCKER) and, even on clean data, silently breaks the customer hard-delete endpoint via default `NO ACTION` (HIGH).