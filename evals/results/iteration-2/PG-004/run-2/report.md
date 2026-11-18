# Migration Guard Report — PG-004

**Migration reviewed:** `migrations/2026_11_16_add_orders_customer_fk.sql`

```sql
ALTER TABLE orders
  ADD CONSTRAINT orders_customer_fk
  FOREIGN KEY (customer_id) REFERENCES customers (id);
```

**Engine:** PostgreSQL 14 · **Deploy mode:** single transaction · **Table size:** `orders` ~7,004,118 rows, `customers` ~180k rows

**Verdict: ❌ BLOCKED — do not merge.** The migration will fail at apply time against real data, and even a "fixed" version using `NOT VALID` alone leaves a production break in the existing delete endpoint.

---

## Findings

### 🔴 BLOCKER 1 — Orphan rows make `ADD CONSTRAINT` fail; the migration aborts

**Evidence (schema.sql, data facts of 2026-11-16):**
> `WHERE o.customer_id IS NOT NULL AND c.id IS NULL;` → **22,913 — orphans**

**Evidence (schema.sql):**
> `-- FK status: none exists on orders.customer_id.`

`ALTER TABLE … ADD CONSTRAINT … FOREIGN KEY` validates **every** existing row by default (no `NOT VALID` is specified). With 22,913 rows pointing at customers that do not exist, PostgreSQL will reject the constraint and raise `SQLSTATE[23503]` "violates foreign key constraint orders_customer_fk". Because **"The deploy pipeline wraps all migrations in a single transaction"** (context.md), the entire migration rolls back — the deploy fails, it does not partially succeed.

This is deterministic, not a load/race condition: the orphan count is a static data fact. The migration **cannot** be merged as written.

**Required remediation (one of):**
1. Resolve the 22,913 orphans first (set `customer_id = NULL` for them, backfill correct customers, or archive), in a **separate, earlier, idempotent** migration; verify `SELECT count(*) … WHERE c.id IS NULL` returns `0` before this FK migration runs; **or**
2. Add the FK with `NOT VALID` so addition does not scan/validate existing rows, then `VALIDATE CONSTRAINT` in a later step *after* the orphans are cleaned. Note: `NOT VALID` on its own is **not** a fix while orphans still exist in the table — new inserts are checked, and the constraint can never be validated until the data is fixed. (Validation takes `SHARE UPDATE EXCLUSIVE`, which permits concurrent writes — acceptable for 7M rows.)

---

### 🟠 HIGH 2 — Default `NO ACTION` breaks the existing hard-delete endpoint

**Evidence (context.md):**
> There is **no `ON DELETE` clause** in the proposed FK, so the default is `NO ACTION`.

**Evidence (application-usage.md, deletion path):**
> `$entityManager->remove($customer); $entityManager->flush();` → hard `DELETE FROM customers WHERE id = ?`. There is **no cascade to `orders`** and no `ON DELETE` behaviour implemented anywhere. With an FK using the default `NO ACTION`, **this endpoint starts throwing `SQLSTATE[23503]`** for every existing customer that has orders — which is **almost all of them**.

The developer's statement in context.md — *"orders are supposed to cascade. Should be fine."* — is **contradicted by the code**. No `ON DELETE CASCADE` exists; the FK as proposed reinforces the default `NO ACTION` and will make admin customer deletion fail for essentially every customer with orders (≈ 6.98M of 7.0M orders reference customers). This is a functional regression introduced by the migration on deploy, not at a later date.

**Required remediation:** make an explicit, deliberate choice:
- `ON DELETE CASCADE` — dangerous: **deletes up to ~7M orders** when a single customer row is removed (and the admin endpoint does exactly that). Should almost certainly be rejected absent a business decision.
- `ON DELETE SET NULL` — matches the current tolerant read path (`leftJoin('o.customer', 'c')`; UI tolerates `NULL` joins per OrderRepository.php:34) and the fact `customer_id` is already nullable. **Recommended.**
- `ON DELETE RESTRICT`/`NO ACTION` — only if the delete endpoint is changed to soft-delete or blocked intentionally; that change must ship **before** the FK.

Whichever is chosen, the delete endpoint (`AdminCustomerController.php:120`) and the "not searched" **admin UI error handling** must be verified against the new behaviour.

---

### 🟡 MEDIUM 3 — Orphan source indicates data-integrity risk, and writes do not guard against recurrence

**Evidence (schema.sql):**
> Orphans come from a 2022 data import where the customer table was rebuilt and IDs changed.

**Evidence (application-usage.md, writes):**
> `LegacyOrderImport.php:61` — inserts orders with `customer_id` from a CSV **without** checking that the customer exists.

Once the FK is live, the legacy import will begin **failing** on any CSV row with an unknown customer (and `customer_id` is nullable but the import apparently does not emit `NULL` for unknown customers). The "form layer" `exists:customers,id` validation (OrderController.php:88) only covers the web path, not the import. Confirm with the data owner whether import CSVs can contain unknown IDs; if so, the import must map unknown IDs to `NULL` (or the migration must be scheduled after that change). Also note the import is a plausible source of the very orphans we have to clean in BLOCKER 1.

---

### 🟡 MEDIUM 4 — `NOT VALID` alone is being skipped as a solution, but still needs the lock discussion

For a 7M-row table, adding a validated FK makes PostgreSQL take a `SHARE ROW EXCLUSIVE`-level lock and scan/index-check all rows in one statement. With the single-transaction deploy this means the lock is held until migration end, blocking writes to `orders` for the duration. After the orphans are cleaned, prefer add-with-`NOT VALID` then `VALIDATE CONSTRAINT` to keep the exclusive period short. (Not a blocker by itself because BLOCKER 1 must be fixed first anyway.)

---

## What this report does **not** confirm

- **Admin UI error handling for the delete endpoint** — explicitly marked "Not searched" in application-usage.md. If the UI does not surface 23503 gracefully, users will see unhandled errors immediately after deploy. Verify before shipping.
- No read query is broken by adding an FK: OrderRepository.php:34 uses a `LEFT JOIN` and tolerates `NULL`, which is consistent with `ON DELETE SET NULL`.

---

## Required before merge

1. **BLOCKER:** ship a data migration that eliminates all 22,913 orphans (and prove the count is 0). Then add the FK (`NOT VALID` + later `VALIDATE` recommended).
2. **HIGH:** add an explicit `ON DELETE` clause; recommended `SET NULL` to match the tolerant read path. If RESTRICT/NO ACTION, the hard-delete endpoint must be fixed **before** the FK ships.
3. **MEDIUM:** verify `LegacyOrderImport` handles unknown `customer_id` (map to `NULL`) before the FK goes live; confirm admin UI error handling.

**Status: ❌ BLOCKED — not safe to merge.**