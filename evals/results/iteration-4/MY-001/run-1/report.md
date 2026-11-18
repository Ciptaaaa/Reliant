# Migration Guard Report

## Scope
- **Migration**: `database/migrations/2026_11_16_120000_drop_supplier_contact_phone.php`
- **Engine**: MySQL 8.0.36 (InnoDB)
- **Inspected**: `migration.sql`, `schema.sql`, `application-usage.md`, `context.md`
- **Not inspected**: the ERP system itself (external; it consumes the nightly export file)
- **Unknowns**: whether the ERP consumer tolerates a missing field is not stated

## Verdict
**BLOCKED** — the column is still read by the supplier form, the purchase order PDF, and the nightly ERP export, so dropping it breaks all three on deploy.

## Findings

### F1 — BLOCKER — destructive-column-drop
- **Statement**: `ALTER TABLE suppliers DROP COLUMN contact_phone` in `migration.sql:2` — removes the column, its ~3,100 non-NULL values, and `suppliers_contact_phone_idx`
- **Evidence**:
  - `application-usage.md:5` — `app/Models/Supplier.php:21` lists `contact_phone` in `$fillable`, so writes still target it
  - `application-usage.md:16` — `resources/views/purchase-orders/pdf.blade.php:63` prints `contact_phone` on every purchase order PDF
  - `application-usage.md:22` — `app/Services/ErpExport.php:37` selects `contact_phone` in the nightly export that runs from the deployed image
  - `migration.sql:2` — the drop is unconditional; `context.md:16` confirms MySQL DDL commits immediately
- **Impact**: after the drop the supplier form fails on save, the purchase order PDF raises `SQLSTATE[42S22] Column not found`, and the nightly ERP export stops until a new image ships; the ~3,100 phone numbers are deleted
- **Mitigation**: retire every reader and writer in an earlier release (fillable list, validation, PDF, ERP export), deploy it, confirm the old tasks are drained, then drop in a separate migration. Because the drop deletes the ~3,100 values, recovery is a named RDS point-in-time restore (5-minute RPO) taken immediately before the drop; archive the column values first if they must survive.
- **Verification**: `grep -rn "contact_phone" app/ resources/ database/` in the deployed image returns nothing, and the ERP export has run once against a replacement field
- **Approval**: accept-risk
- **Reversible**: no

### F2 — HIGH — data-integrity
- **Statement**: the same `DROP COLUMN` destroys the ~3,100 stored phone numbers, and MySQL DDL cannot be rolled back
- **Evidence**:
  - `schema.sql:14` — "About 3,100 rows have a non-NULL `contact_phone`"
  - `migration.sql:2` — the statement that removes them
  - `context.md:16` — "MySQL DDL does not roll back; if the statement succeeds it is committed immediately"
- **Impact**: the values are gone the instant the DDL commits; recovery means a point-in-time restore of the whole instance, which also reverts unrelated tables
- **Mitigation**: archive `contact_phone` into a dedicated table or export file in the same window if the data must be preserved, and record the restore point explicitly
- **Verification**: the archived row count equals `SELECT COUNT(*) FROM suppliers WHERE contact_phone IS NOT NULL`
- **Approval**: accept-risk
- **Reversible**: no

### F3 — INFO — no-risk-observed
- **Statement**: checked lock impact and the index dropped with the column
- **Evidence**:
  - `schema.sql:14` — `suppliers` holds about 4,800 rows, and `context.md:11` says low write traffic, so the metadata lock is brief
  - `schema.sql:11` — `suppliers_contact_phone_idx` covers `contact_phone` only, and no query depends on that index alone
  - `application-usage.md:29` — "No database views or generated columns use `contact_phone`"
- **Impact**: none. The "small table, lock is fine" framing is correct about the lock and wrong about the risk: the hazard is compatibility and data loss, not locking.
- **Mitigation**: none
- **Verification**: `SHOW INDEX FROM suppliers` after the drop lists no `suppliers_contact_phone_idx`
- **Approval**: none
- **Reversible**: yes

## Safer migration path
1. Deploy a release that stops reading and writing `contact_phone`: `app/Models/Supplier.php:21`, `app/Http/Controllers/SupplierController.php:44`, `resources/views/purchase-orders/pdf.blade.php:63`, `app/Services/ErpExport.php:37`.
2. Confirm the ERP export ran once against the replacement field.
3. Wait one full ECS rolling update and confirm the old tasks are drained.
4. Archive the column values if they must be preserved; record the restore point.
5. Drop in a separate migration with a human approval attached: `ALTER TABLE suppliers DROP COLUMN contact_phone;`
6. Verify: `SHOW COLUMNS FROM suppliers` no longer lists `contact_phone`.

## Rollback and recovery
- **Schema rollback**: `ALTER TABLE suppliers ADD COLUMN contact_phone VARCHAR(32) DEFAULT NULL;` restores the structure, but the column comes back empty and the index must be recreated.
- **Data recovery**: only from the archive taken in step 4, or a named point-in-time restore of the RDS instance with its 5-minute RPO.
- **Application rollback**: safe before step 5 because the code tolerates the column; after step 5, rolling back breaks the PDF and the ERP export again.

## Pre-deploy checklist
- [ ] Human approval recorded for the `accept-risk` drop
- [ ] Every reader and writer retired and verified in the deployed image
- [ ] ERP export confirmed against the replacement field
- [ ] Archive or named restore point recorded
- [ ] Old ECS tasks fully drained