## Migration Guard Report — `2026_11_16_133000_create_invoice_drafts.php` (raw SQL)

**Verdict: APPROVED**

**Risk: LOW**

### Summary

The migration issues a single `CREATE TABLE `invoice_drafts`` on MySQL 8.0.36 / InnoDB. This is an entirely additive, new-object migration. The table is brand new, contains no data, has no consumers, and has no foreign-key relationships in either direction. On MySQL 8.0, DDL is executed through the atomic data dictionary, so there is no partial-DDL state and no existing data to rewrite.

### Evidence relied on

- **Additive only.** The migration body is nothing but:
  > `CREATE TABLE `invoice_drafts` (...)`
  
  There is no `ALTER`, no `DROP`, no `UPDATE`/`DELETE`/backfill, and no modification of the existing `accounts` table.

- **New, empty table.** Per `context.md`:
  > "**Table**: new, empty."
  
  No data is at risk because there is no data.

- **No consumers.** Per `application-usage.md`:
  > "No references: `grep -rn "invoice_drafts" app/ database/ resources/ routes/` returns nothing except this migration."
  > "The invoicing feature branch is not merged; when merged it will be the first consumer."
  
  There is no read/write path that a schema failure could break, and no backfill requirement is triggered.

- **No FK either direction.** Per `application-usage.md`:
  > "No foreign key to `accounts` is created in this migration (an index on `account_id` exists, so a later FK can be added without an implicit index build)."
  
  And per the index definition:
  > `KEY `invoice_drafts_account_id_idx` (`account_id`)`
  
  No FK means no referential-integrity validation pass, no parent-row lock concerns, and no orphan-data risk. The supporting index is already in place, which makes a future FK clean.

- **MySQL 8.0 atomic DDL.** Per `context.md`:
  > "On MySQL 8.0, the `CREATE TABLE` uses the atomic data dictionary; no data is at risk."

### Findings

No blocking or warning-level findings.

### Explicitly not flagged (false positives avoided)

- **"Empty table is a risk"** — not a risk. There is no data, so there is nothing to protect or migrate.
- **"Needs a backfill"** — not applicable. The table is new and has no rows or consumers to populate.
- **"Missing FK is a problem"** — not a problem in this scoped change. The `account_id` index is present, so the FK can be added later without an implicit index build. Adding the FK now would be a separate, optional follow-up, not a precondition for this migration.

### Recommendation

Merge. Optionally, when the invoicing feature branch lands and the real read/write path exists, consider a follow-up migration to add the `account_id` → `accounts.id` foreign key; the existing index means that follow-up is cheap. That is not required for this migration.