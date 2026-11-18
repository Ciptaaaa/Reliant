# Migration Guard Report — PG-004

**File:** `migrations/2026_11_16_add_orders_customer_fk.sql`
**Engine:** PostgreSQL 14
**Verdict:** ❌ **BLOCK — do not merge as-is.**

---

## Findings

### 1. BLOCKER — Migration will fail on existing orphan rows

**Migration:**
```sql
ALTER TABLE orders
  ADD CONSTRAINT orders_customer_fk
  FOREIGN KEY (customer_id) REFERENCES customers (id);
```

PostgreSQL validates existing rows when adding an FK, so the `ALTER` fails if any `orders.customer_id` has no matching customer.

**Evidence (`schema.sql`):**
> ```
> SELECT count(*) FROM orders o
>   LEFT JOIN customers c ON c.id = o.customer_id
>  WHERE o.customer_id IS NOT NULL AND c.id IS NULL;   -> 22,913  -- orphans
> ...
> Orphans come from a 2022 data import where the customer table was rebuilt and IDs changed.
> ```

**Consequence:** About 7M rows are scanned, and 22,913 violations produce:
```
ERROR: insert or update on table "orders" violates foreign key constraint "orders_customer_fk"
DETAIL: Key (customer_id)=(...) is not present in table "customers".
```

Because the pipeline wraps migrations in a single transaction, the whole migration rolls back. `NOT VALID` alone does not solve this; the constraint may be added but stays unvalidated, and PostgreSQL rejects every new insert or update that references a missing customer, including `LegacyOrderImport.php:61`, which inserts unchecked `customer_id` values from CSV.

**Note:** `customer_id IS NULL` is 0 today, so NULLs are not a problem *now*; the FK allows NULL by default. But `LegacyOrderImport.php:61` can produce NULL or bad values later, and with a validated FK each would fail at insert.

### 2. HIGH — Default `NO ACTION` breaks the existing hard-delete endpoint

The proposed constraint has no `ON DELETE` clause, and the context confirms:
> "There is no `ON DELETE` clause in the proposed FK, so the default is `NO ACTION`."

The current delete path is:
```php
// AdminCustomerController.php:120
$entityManager->remove($customer);
$entityManager->flush();
```
> "→ hard `DELETE FROM customers WHERE id = ?`. There is no cascade to `orders` and no `ON DELETE` behaviour implemented anywhere. With an FK using the default `NO ACTION`, this endpoint starts throwing `SQLSTATE[23503]` ("violates foreign key constraint") for every existing customer that has orders — which is almost all of them."

The developer's assumption — *"orders are supposed to cascade. Should be fine."* — is **false** for this migration. No cascade exists in code, and the proposed FK does not add one.

### 3. MEDIUM — `deleted_at` suggests an intended alternative to hard-delete

`customers.deleted_at timestamptz` exists with the comment `-- soft delete is NOT used consistently`. The delete endpoint ignores it, so I cannot tell from the given files whether soft delete is the intended path. That affects *which* fix is correct (cascade vs `RESTRICT` + soft delete), but not the verdict.

---

## Required changes before merge

Do these as **separate** migrations. The orphan cleanup must not run in the same transaction as the FK change.

1. **Decide orphan semantics and clean data first.** For the 22,913 orphans, either:
   - set `customer_id = NULL` (FK allows NULL; UI tolerates it: `OrderRepository.php:34` does a `leftJoin` and "the UI tolerates `NULL` joins"), **or**
   - backfill the correct customer IDs from the 2022 mapping, if one exists.
   Verify `count(*) … WHERE c.id IS NULL` is 0 before proceeding.
2. **Fix write paths before enforcing.** `LegacyOrderImport.php:61` must validate `customer_id` (or insert NULL) before the constraint is `VALIDATED`, otherwise imports stop working.
3. **Add the FK with an explicit `ON DELETE` clause:**
   - If hard-delete should remove orders: `ON DELETE CASCADE`. This deletes millions of orders for popular customers; confirm that is intended.
   - If customers with orders must not be deleted: `ON DELETE RESTRICT` **and** change `AdminCustomerController.php:120` to soft-delete (`deleted_at`) and/or return a clear error. Also check that the admin UI handles the error, which was not searched.
4. **Ship with `NOT VALID`, then validate separately:**
   ```sql
   ALTER TABLE orders
     ADD CONSTRAINT orders_customer_fk
     FOREIGN KEY (customer_id) REFERENCES customers (id)
     ON DELETE <CASCADE|RESTRICT>
     NOT VALID;
   -- separate migration after cleanup, not in the single deploy transaction:
   ALTER TABLE orders VALIDATE CONSTRAINT orders_customer_fk;
   ```
   `ADD CONSTRAINT … NOT VALID` takes only a brief `SHARE ROW EXCLUSIVE` lock and skips the full scan; validation later takes `SHARE UPDATE EXCLUSIVE` and permits concurrent reads/writes. Do not run the full validating `ALTER` on 7M rows inside the single-transaction pipeline.

---

## Summary

| # | Severity | Issue |
|---|----------|-------|
| 1 | **BLOCKER** | 22,913 orphan `customer_id`s cause the FK to fail at add time (and full rollback, given the single-transaction pipeline). |
| 2 | **HIGH** | Default `NO ACTION` breaks the hard-delete endpoint (`AdminCustomerController.php:120`) with `SQLSTATE[23503]`. The "orders cascade" claim is unsupported. |
| 3 | MEDIUM | `LegacyOrderImport.php:61` does not validate `customer_id`s and will break once the FK is enforced. |
| 4 | MEDIUM | `deleted_at` exists but is unused; intended delete semantics are unresolved. |

**Verdict: BLOCK.** The migration cannot succeed on current data, and even after cleanup it introduces a regression in the customer deletion path. `NOT VALID` addresses lock and scan cost, not the invalid data.