# Migration Guard Report — `migrations/2026_11_16_add_orders_customer_fk.sql`

## Verdict: **BLOCK — do not merge as written**

## Statement under review
```sql
-- migrations/2026_11_16_add_orders_customer_fk.sql
ALTER TABLE orders
  ADD CONSTRAINT orders_customer_fk
  FOREIGN KEY (customer_id) REFERENCES customers (id);
```

## Findings

### (a) BLOCKER — 22,913 existing orphan rows make the constraint fail

- `migration.sql` adds the FK with no `NOT VALID`, so PostgreSQL validates every existing row **inside the migration transaction**. Per `context.md`: "The deploy pipeline wraps all migrations in a single transaction."
- `schema.sql` data facts:
  ```sql
  SELECT count(*) FROM orders o
    LEFT JOIN customers c ON c.id = o.customer_id
   WHERE o.customer_id IS NOT NULL AND c.id IS NULL;   -- -> 22,913
  ```
  > "FK status: none exists on `orders.customer_id`."
  > "Orphans come from a 2022 data import where the customer table was rebuilt and IDs changed."
- With those rows present, the `ALTER TABLE` raises `SQLSTATE[23503]` ("violates foreign key constraint"), the transaction rolls back, and **the whole deploy fails**.

This is exactly the trap flagged in `context.md`: *"A report that only says 'use `NOT VALID` and it is fine' has skipped the orphan rows."*

### (b) HIGH — Compatibility risk: `NO ACTION` breaks the existing hard-delete endpoint

- The developer claims: *"orders are supposed to cascade. Should be fine."* **No cascade exists anywhere.**
- `application-usage.md`:
  - `AdminCustomerController.php:120`: `$entityManager->remove($customer); $entityManager->flush();`
    > "hard `DELETE FROM customers WHERE id = ?`. There is no cascade to `orders` and no `ON DELETE` behaviour implemented anywhere."
  - `schema.sql` documents no `ON DELETE` behaviour on `orders`.
- `migration.sql` has no `ON DELETE` clause, so the default is `NO ACTION`. Once the FK is live, **every delete of a customer that has orders throws `SQLSTATE[23503]`** — "which is almost all of them." This is a functional regression on an existing admin path, not just a data issue.

### (c) MEDIUM — `NOT VALID` alone does not fix the delete path

Using `NOT VALID` makes the `ALTER` succeed despite the 22,913 orphans, but:
- It does **not** address (a) for future data: new `INSERT`/`UPDATE` on `orders` is still validated, and **new orphans become impossible to insert** — which breaks `LegacyOrderImport.php:61`, described as inserting `customer_id` from a CSV **"without checking that the customer exists."** That import will start failing on any row whose customer is missing.
- It does **not** address (b): a `NOT VALID` FK still enforces `NO ACTION` on deletes.
- It leaves 22,913 live orphans that will surface in `OrderRepository.php:34` (`leftJoin('o.customer','c')`) as permanent null joins; the UI already tolerates `NULL` ("orders without a matching customer currently render an empty customer block"), so this is a data-integrity debt, not a crash.

### (d) MEDIUM — Deployment risk: `ALTER TABLE` lock on a 7M-row table

- `context.md`: "`orders` ~7M rows" with a 7,004,118-row table. `ADD FOREIGN KEY` takes a `SHARE ROW EXCLUSIVE` lock, which blocks writes; validation then **must read all 7M rows** (or at least all non-null `customer_id` rows — currently all of them, since `customer_id IS NULL` count is 0). With a single-transaction deploy, this holds locks for the duration. Even with clean data, this should be staged, not run inline in the merge deploy. `NOT VALID` reduces the validation cost but still takes the lock at `ALTER` time; a concurrent build via `VALIDATE CONSTRAINT` is the proper pattern.

## Remediation plan (all required before merge)

1. **Resolve or quarantine the 22,913 orphans.** Options: re-point them to a real customer (e.g., a sentinel `customers` row with `id = 0` or a `deleted` customer), set `customer_id = NULL` (verified safe: currently `customer_id IS NULL` count is 0 and UI tolerates nulls), or delete the orders (likely unacceptable). Do this as a separate, rehearsed migration. Confirm in pre-prod that `LEFT JOIN` orphan count is 0.
2. **Add the FK as `NOT VALID`, then `VALIDATE CONSTRAINT` separately** — do not put validation in the same transactional deploy. Example:
   ```sql
   ALTER TABLE orders
     ADD CONSTRAINT orders_customer_fk
     FOREIGN KEY (customer_id) REFERENCES customers (id)
     NOT VALID;
   -- later, outside the deploy transaction:
   ALTER TABLE orders VALIDATE CONSTRAINT orders_customer_fk;
   ```
3. **Choose explicit `ON DELETE` behaviour.** Given `AdminCustomerController.php:120` hard-deletes customers and orders must survive (they carry financial/history data), `ON DELETE SET NULL` fits the UI's null-tolerance and avoids deleting financial records; `ON DELETE CASCADE` would silently delete ~7M orders if a customer is removed — almost certainly wrong. The developer's "orders are supposed to cascade" claim is not backed by any code in `application-usage.md`. **This decision must be signed off explicitly**, and `AdminCustomerController.php:120` should be reviewed to ensure it handles the resulting `customer_id = NULL\]` state and surfaces errors (the admin UI's error handling was not searched — open item).
4. **Fix `LegacyOrderImport.php:61`** to validate or create missing customers (or tolerate a controlled null) before the FK enforces. Otherwise the import becomes a latent breakage.
5. Add an index on `customers(id)` — already the PK, so fine. But verify the FK's `orders_customer_id_idx` (`schema.sql`) is used for the reverse delete check; it is.

## Do **not** accept

- A report saying "just add `NOT VALID`" — it skips the 22,913 orphans and the `NO ACTION` delete breakage, both of which are the point of PG-004.
- The developer's claim "orders are supposed to cascade. Should be fine." — no cascade exists in any file; the default is `NO ACTION`.

## Sign-off checklist

- [ ] Orphan rows resolved; pre-prod verification query returns 0
- [ ] FK added `NOT VALID`, `VALIDATE` run as a separate step
- [ ] Explicit `ON DELETE` decision documented and matched to `AdminCustomerController.php:120` behaviour
- [ ] Admin delete endpoint error handling reviewed (not yet searched)
- [ ] `LegacyOrderImport.php:61` validated/hardened
- [ ] Lock/no-transaction impact assessed for the 7M-row table

**Status: does not meet merge criteria; blocked on (a) and (b).**