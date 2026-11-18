# Example 2 — A `BLOCKED` report (MySQL, destructive drop)

The same migration shape as `tests/migration-guard/cases/mysql/MY-001`, with the report written out in
full. Read this when you need to see what the blocking evidence chain looks like end to end.

## Input

`schema.sql`

```sql
CREATE TABLE suppliers (
  id            BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  name          VARCHAR(160) NOT NULL,
  contact_phone VARCHAR(32)  DEFAULT NULL,
  PRIMARY KEY (id),
  KEY suppliers_contact_phone_idx (contact_phone)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
-- MySQL 8.0.36, ~4,800 rows
```

`migration.sql`

```sql
ALTER TABLE suppliers DROP COLUMN contact_phone;
```

`application-usage.md` (excerpt) — three references that read the column, one of them a nightly export
job that runs from the last released image.

## Report

```markdown
# Migration Guard Report

## Scope
- **Migration**: database/migrations/2026_11_16_drop_supplier_contact_phone.php
- **Engine**: MySQL 8.0.36 (InnoDB)
- **Inspected**: schema.sql, migration.sql, app/Models, app/Http, app/Services, resources/views,
  database/seeders
- **Not inspected**: the ERP system that consumes the nightly export; mobile clients
- **Unknowns**: whether the ERP consumer can be changed before the drop; the deployment order relative
  to the nightly export job

## Verdict
**BLOCKED** — the column is still read by the supplier form, the purchase order PDF, and the nightly
ERP export.

## Findings

### F1 — BLOCKER — destructive-column-drop
- **Statement**: `ALTER TABLE suppliers DROP COLUMN contact_phone` in `migration.sql:2` — removes the column and its ~3,100 non-NULL values
- **Evidence**:
  - `app/Models/Supplier.php:21` — lists `contact_phone` in `$fillable`
  - `app/Services/ErpExport.php:37` — selects `contact_phone`; the nightly ERP export runs from the last released image
  - `resources/views/purchase-orders/pdf.blade.php:63` — the purchase order PDF prints `contact_phone`
  - `migration.sql:2` — the drop
- **Impact**: after the drop the supplier form fails on save, the purchase order PDF raises `SQLSTATE[42S22] Column not found`, and the nightly ERP export stops until a new image is released
- **Mitigation**: stop reading and writing the column and migrate the ERP export to a replacement field in an earlier release; drop in a separate migration after one full deploy cycle
- **Verification**: `grep -rn "contact_phone" app/ resources/ database/` returns no results, and the ERP export has run once against the replacement field
- **Approval**: accept-risk
- **Reversible**: no

### F2 — HIGH — data-integrity
- **Statement**: the same statement, `migration.sql:2` — MySQL DDL commits implicitly, so the values go with it
- **Evidence**:
  - `schema.sql:14` — about 3,100 non-NULL `contact_phone` values exist
  - `context.md:17` — MySQL DDL does not roll back; the only restore path is point-in-time recovery with 7-day retention and up to 5-minute RPO
- **Impact**: about 3,100 phone numbers are permanently removed; recovery reverts unrelated tables to that moment
- **Mitigation**: archive the column to a table or export file in the same window if the data must be preserved; otherwise record the restore point explicitly
- **Verification**: archived row count matches `SELECT count(*) FROM suppliers WHERE contact_phone IS NOT NULL`
- **Approval**: accept-risk
- **Reversible**: no

### F3 — INFO — no-risk-observed
- **Statement**: checked lock impact, the attached index, and DB-side dependencies
- **Evidence**:
  - `schema.sql:14` — `suppliers` has about 4,800 rows, so the metadata lock is brief
  - `schema.sql:11` — `suppliers_contact_phone_idx` is attached to `contact_phone`; no query depends on that index alone
  - `application-usage.md:25` — no views or generated columns reference the column
- **Impact**: none. The risk in this migration is compatibility and data loss, not locking.
- **Mitigation**: none
- **Verification**: `SHOW INDEX FROM suppliers` after the drop lists no `suppliers_contact_phone_idx`
- **Approval**: none
- **Reversible**: yes

## Safer migration path
1. Add `contact_phone_archive` and copy the values (batched).
2. Release code that no longer reads or writes `contact_phone`, including the ERP export.
3. Run the ERP export once against the replacement field and confirm the consumer accepts it.
4. In a separate gated migration, `DROP COLUMN contact_phone`.

## Rollback and recovery
- **Schema rollback**: `ADD COLUMN contact_phone VARCHAR(32)` restores the structure but not the values.
- **Data recovery**: requires point-in-time restore; RPO up to 5 minutes, 7-day retention. Without a
  confirmed restore point this drop is a BLOCKER.
- **Application rollback**: version N-1 of the application and the export job both need `contact_phone`,
  so rolling back after the drop fails with "unknown column".

## Pre-deploy checklist
- [ ] Human approval recorded for the drop
- [ ] ERP export migrated and verified
- [ ] Archive of `contact_phone` written and row count matched
- [ ] Restore point recorded, with RPO/RTO stated
- [ ] `grep` proof that no code references the column
```

## Why this is the right report

| Trap | What the example does instead |
|---|---|
| "The table is small, so the lock is fine" | Agrees, rates lock `INFO`, and keeps the blocker on compatibility |
| Approving because the user said "not used" | Quotes three code paths that contradict the claim |
| Treating "rollback" as one thing | Splits schema, data, and application rollback, and states that schema rollback loses the values |
| Recommending `CASCADE` to make the drop work | Recommends the additive path instead |