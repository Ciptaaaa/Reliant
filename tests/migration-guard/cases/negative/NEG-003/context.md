# NEG-003 — Create a table with no consumers yet

## Context

- **Engine**: MySQL 8.0.36, InnoDB
- **Application**: Laravel 11
- **What the developer said**:
  > We are starting the invoicing feature next sprint. Creating the table now so the schema team can
  > review it: `CREATE TABLE invoice_drafts (...)`.
- **Table**: new, empty.

## Deployment facts

- No code reads or writes `invoice_drafts` yet (the feature branch is not merged).
- No FK from or to the new table.

## Why this case exists (negative)

Creating a new empty table is a new, additive migration. On MySQL 8.0, the `CREATE TABLE` uses the
atomic data dictionary; no data is at risk. Correct answer: **APPROVED** with a `LOW` finding. A report
that flags "empty table" as a risk, or asks for a backfill, is a false positive.
