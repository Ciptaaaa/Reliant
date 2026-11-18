# PG-004 — Expected analysis

## Expected verdict

**BLOCKED** — 22,913 orphan rows make the FK fail; and the proposed FK breaks the existing customer
delete path.

## Findings

### F1 — `BLOCKER` — `foreign-key` — orphan `customer_id` values

`schema.sql` records 22,913 orders whose `customer_id` points to a customer that no longer exists
(a 2022 import rebuilt IDs). `ALTER TABLE orders ADD CONSTRAINT … FOREIGN KEY` validates existing rows
and fails with `insert or update on table "orders" violates foreign key constraint`, naming a sample
orphan. The migration cannot be applied.

`NOT VALID` would let you create the constraint without the scan, but **it does not fix the orphans**:
the new inserts are checked, the existing 22,913 rows stay orphaned, and a later `VALIDATE CONSTRAINT`
fails until they are resolved. Do not report `NOT VALID` as a complete mitigation.

**Evidence required:** `schema.sql` (22,913), `migration.sql`,
`src/Import/LegacyOrderImport.php` (no existence check), `application-usage.md`.

**Mitigation:**

1. Decide the data fix with the business: re-link orphans to a real customer, set `customer_id = NULL`
   (orders already tolerate a `NULL` customer per the repository's `leftJoin`), or delete them.
2. Backfill in batches, verifying counts (`LEFT JOIN … WHERE c.id IS NULL` → 0).
3. Add the FK with `NOT VALID` inside the deploy, then `VALIDATE CONSTRAINT` in a separate, monitored
   step (`SHARE UPDATE EXCLUSIVE`, does not block writes).
4. Fix `LegacyOrderImport.php` to verify the customer exists, or it will recreate orphans.

**Approval: accept-risk (F1)** — a human accepts the loss of the old values; a recovery plan and restore point are required.

### F2 — `HIGH` — `application-compatibility` — the default `NO ACTION` breaks hard delete

`src/Controller/AdminCustomerController.php:120` hard-deletes customers. The proposed FK has no
`ON DELETE` clause, so it defaults to `NO ACTION`: every delete of a customer that has orders now
fails with `SQLSTATE[23503]`. `context.md` says "orders are supposed to cascade", but no cascade
exists in the code or the FK, so this is a behaviour change in a live admin workflow.

**Evidence required:** `application-usage.md` (AdminCustomerController), `migration.sql`.

**Mitigation:** choose the intended semantics explicitly — `ON DELETE CASCADE` (deletes order
history: dangerous), `ON DELETE SET NULL` (preserves orders, needs the column nullable — it is), or
`ON DELETE RESTRICT` plus a soft-delete change in the admin endpoint. Whichever is chosen must be
matched by the application change, and the choice belongs to the product owner.

### F3 — `MEDIUM` — `lock-and-rewrite` — validation scans 7M rows

Validating the FK scans all 7M orders. With `ALTER TABLE … ADD FOREIGN KEY` (no `NOT VALID`) the
scan happens inside the `ADD` and holds `SHARE ROW EXCLUSIVE` on both tables for the entire scan;
writes to `orders` and `customers` that touch the constraint wait. `NOT VALID` + `VALIDATE` keeps the
`ADD` instantaneous and moves the scan to `SHARE UPDATE EXCLUSIVE`.

**Mitigation:** `NOT VALID` then `VALIDATE CONSTRAINT` in a low-traffic window; monitor the validate.

### F4 — `INFO` — `no-risk-observed` — no read breakage

No code path requires `orders` to have a valid customer: the join is a `leftJoin` and the UI renders
an empty block. Reads are unaffected by the FK.

## Unknowns that must be reported

- Business decision on orphan handling (delete, null, re-link) is not derivable from the repository.
- Desired delete semantics (cascade vs restrict vs soft delete) needs a product decision.
- The admin UI's error handling is not in scope of what was inspected.

## Verdict reasoning

Orphans + FK = certain failure and data-integrity work → `BLOCKER` → `BLOCKED`. A report that says
"use `NOT VALID` so it is safe" is the exact failure mode this case is designed to catch.
