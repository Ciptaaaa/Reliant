# Migration Guard Report — `migrations/2026_11_16_add_orders_customer_fk.sql`

## Verdict: ❌ BLOCKED — do not merge

Two independent defects. The first guarantees the migration fails on deploy; the second breaks an existing admin feature even after the data is cleaned.

---

## Blocker 1 — Orphan rows will fail the FK validation

The migration:

```sql
ALTER TABLE orders
  ADD CONSTRAINT orders_customer_fk
  FOREIGN KEY (customer_id) REFERENCES customers (id);
```

PostgreSQL validates a new FK against existing rows immediately (unless `NOT VALID`). The data facts in `schema.sql`:

```sql
SELECT count(*) FROM orders o
  LEFT JOIN customers c ON c.id = o.customer_id
 WHERE o.customer_id IS NOT NULL AND c.id IS NULL;   -> 22,913  -- orphans
```

22,913 rows reference `customers` IDs that do not exist. Origin, per `schema.sql`:

> "Orphans come from a 2022 data import where the customer table was rebuilt and IDs changed."

This is not a race or theoretical edge case: the constraint validation scans every row and fails on the first orphan. The deploy pipeline wraps all migrations in one transaction, so nothing partial is left behind, but the deploy fails cleanly and the migration never merges.

Also noted: `SELECT count(*) FROM orders WHERE customer_id IS NULL; -> 0`. So currently every order has a non-NULL `customer_id`; all nullability confusion can be set aside, but this does not help the FK.

**What must happen before merge:** a data-remediation decision is required (backfill orphan IDs to a valid customer, map the old IDs, or set them to NULL if the app truly tolerates NULL customer joins — see the read path below), then add the FK with `NOT VALID` and later `VALIDATE CONSTRAINT` to avoid a long `ALTER` lock on the ~7M-row table during the rolling deploy.

The read path tolerates NULLs, which matters for remediation options:

> `$qb->leftJoin('o.customer', 'c')->addSelect('c');`
> → "orders without a matching customer currently render an empty customer block; the UI tolerates `NULL` joins." (`application-usage.md`, Reads)

## Blocker 2 — `NO ACTION` breaks the customer delete endpoint

The FK has no `ON DELETE` clause. Postgres default is `NO ACTION`. The delete path is a hard `DELETE`:

```php
$entityManager->remove($customer);
$entityManager->flush();
```

`application-usage.md` states:

> "There is no cascade to `orders` and no `ON DELETE` behaviour implemented anywhere. With an FK using the default `NO ACTION`, this endpoint starts throwing `SQLSTATE[23503]` ('violates foreign key constraint') for every existing customer that has orders — which is almost all of them."

The developer's claim is wrong:

> "The customer delete endpoint hard-deletes customers, but orders are supposed to cascade. Should be fine."

"Supposed to cascade" is not implemented. With this FK, every admin delete of a customer that has orders fails. This breaks existing production behaviour the moment the migration lands, independent of the orphan problem.

Also relevant: `orders.customer_id` is nullable, and the app tolerates NULL joins. `ON DELETE SET NULL` is the natural fit, but only if orphan remediation preserves that and the import path stops creating orphans. Do not choose `CASCADE` without deciding what happens to 7M historical orders when a customer is deleted — that is a business decision, not a migration default.

Separately, the import write path is a latent orphan source:

> `src/Import/LegacyOrderImport.php:61` — inserts orders with `customer_id` from a CSV **without** checking that the customer exists.

Once the FK is in place these inserts will start throwing `23503` at runtime. The import needs validation before the FK is enforced, or the FK will simply move the failure from deletion to ingestion.

---

## Summary of required changes

1. **Resolve the 22,913 orphans** (remap, backfill, or NULL them), then add the FK.
2. **Add an explicit `ON DELETE` behaviour** — `SET NULL` matches the nullable column and NULL-tolerant reads; confirm `CASCADE` is not intended.
3. **Use `NOT VALID` + `VALIDATE CONSTRAINT`** to avoid a blocking lock on `orders` during the rolling deploy.
4. **Fix or gate `LegacyOrderImport`** so it cannot insert orphan rows once the constraint is live.
5. Re-check the admin UI's delete error handling (listed as "Not searched" in `application-usage.md`); it will surface `23503` errors until steps 1–2 are done.

Only with all of the above addressed should this migration be re-reviewed.