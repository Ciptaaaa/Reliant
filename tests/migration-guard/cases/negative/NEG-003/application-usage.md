# Application usage: `invoice_drafts`

- No references: `grep -rn "invoice_drafts" app/ database/ resources/ routes/` returns nothing except
  this migration.
- The invoicing feature branch is not merged; when merged it will be the first consumer.
- No foreign key to `accounts` is created in this migration (an index on `account_id` exists, so a later
  FK can be added without an implicit index build).
