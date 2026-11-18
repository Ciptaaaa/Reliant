# Migration Guard Report — NEG-003

## Verdict: **APPROVED** with **LOW** finding

## Change summary

Single statement: `CREATE TABLE invoice_drafts` — a new, empty table with a primary key (`id`) and one secondary index (`invoice_drafts_account_id_idx`). No existing table is altered, no data is moved or transformed, and no rows are touched (`context.md`: "Table: new, empty").

## Evidence review

**1. Purely additive, no data at risk.**
> "Creating a new empty table is a new, additive migration. On MySQL 8.0, the `CREATE TABLE` uses the atomic data dictionary; no data is at risk." — `context.md`

On MySQL 8.0.36 / InnoDB, `CREATE TABLE` only writes the new table to the atomic data dictionary. There is no DDL rebuild of any existing table and no row-level work, so there's no lock or runtime cost on `accounts` or anything else.

**2. No consumers yet — and that's fine.**
> "No code reads or writes `invoice_drafts` yet (the feature branch is not merged)." — `context.md`

> "No references: `grep -rn \"invoice_drafts\" app/ database/ resources/ routes/` returns nothing except this migration." — `application-usage.md`

An empty new table with no readers/writers has no backfill requirement and no compatibility obligation. It can't break any running code path. Creating it before the feature lands is the intended ordering for this case.

**3. No foreign keys in either direction.**
> "No FK from or to the new table." — `context.md`

> "No foreign key to `accounts` is created in this migration (an index on `account_id` exists, so a later FK can be added without an implicit index build)." — `application-usage.md`

`account_id` is indexed (`KEY invoice_drafts_account_id_idx (account_id)`), so a future FK to `accounts.id` can attach without an implicit index build. Referential integrity on `account_id` is not enforced *yet*, but since nothing writes to the table, there are no orphan rows today.

## Findings

| ID | Severity | Finding |
|----|----------|---------|
| F1 | **LOW** | `account_id` has no FK to `accounts(id)`. This is a known, deliberate deferral; the index exists to make adding the FK cheap later. Add the FK in the same migration (or the same release) that introduces the first writer, before any rows exist. |
| F2 | **LOW** (informational) | The table will sit unused until the invoicing branch merges. This is expected and explicitly blessed by the case context; no action needed, but don't forget the schema exists if the feature is deprioritized. |

No MEDIUM or HIGH findings.

## Explicitly not flagged (guards against false positives)

- **"Empty table" is not a risk** and does not need special handling.
- **No backfill is requested or required** — there is no existing data and no consumer.
- **No locking / online-DDL concern** — `CREATE TABLE` doesn't touch existing tables.
- **No rollback data risk** — `DROP TABLE` on an empty table would be trivially safe.

## Recommendation

Merge as-is. Optionally, in the follow-up migration that adds the first consumer, add `FOREIGN KEY (account_id) REFERENCES accounts(id)` to close F1 while the table is still empty.