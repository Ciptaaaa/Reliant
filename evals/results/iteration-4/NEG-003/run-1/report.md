# Migration Guard Report

## Scope
- **Migration**: `database/migrations/2026_11_16_133000_create_invoice_drafts.php`
- **Engine**: MySQL 8.0.36 (InnoDB)
- **Inspected**: `migration.sql`, `schema.sql`, `application-usage.md`, `context.md`
- **Not inspected**: the unmerged invoicing feature branch that will consume the table
- **Unknowns**: none material; the table has no consumers and that is the stated state

## Verdict
**APPROVED** — creating a new empty table is additive, touches no existing data, and has no consumer yet.

> APPROVED means no blocker was found in the inspected scope with the evidence available. This is not a
> production guarantee.

## Findings

### F1 — LOW — operational
- **Statement**: `CREATE TABLE invoice_drafts (...)` in `migration.sql:2-9` — creates a new table with a primary key and one secondary index
- **Evidence**:
  - `migration.sql:2-9` — a single `CREATE TABLE` with `PRIMARY KEY (id)` and `KEY invoice_drafts_account_id_idx (account_id)`
  - `schema.sql:3` — the current schema has only `accounts`; `invoice_drafts` does not exist yet
  - `context.md:3` — MySQL 8.0.36, and the table is new and empty
- **Impact**: on MySQL 8.0 a `CREATE TABLE` is an atomic data-dictionary operation on new objects; no existing row or query is affected and the table starts empty
- **Mitigation**: none required
- **Verification**: `SHOW CREATE TABLE invoice_drafts` matches the migration, and `SELECT COUNT(*) FROM invoice_drafts` returns 0
- **Approval**: none
- **Reversible**: yes

### F2 — INFO — no-risk-observed
- **Statement**: checked whether the new table has consumers or constraints that could fail
- **Evidence**:
  - `application-usage.md:3` — `grep -rn "invoice_drafts" app/ database/ resources/ routes/` returns nothing except this migration
  - `application-usage.md:5` — no foreign key is created in this migration, and the index on `account_id` means a later FK needs no implicit index build
- **Impact**: none. No orphan check, no backfill, and no application path exists to break.
- **Mitigation**: none
- **Verification**: the same grep stays clean until the feature branch merges
- **Approval**: none
- **Reversible**: yes

## Safer migration path
Not needed. This is the additive form: a new table with no consumers.

## Rollback and recovery
- **Schema rollback**: `DROP TABLE invoice_drafts;` — clean while the table has no consumers and no rows.
- **Data recovery**: not applicable; the table starts empty.
- **Application rollback**: safe. No deployed code reads or writes the table.

## Pre-deploy checklist
- [ ] Confirm the feature branch does not merge into the same release
- [ ] Confirm the index on `account_id` exists before any later FK is added