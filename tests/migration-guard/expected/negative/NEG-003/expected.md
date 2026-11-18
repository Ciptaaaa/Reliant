# NEG-003 — Expected analysis

## Expected verdict

**APPROVED** — purely additive: a new, empty table with no consumers.

## Findings

### F1 — `LOW` — `operational` — new empty table, no existing object touched

`CREATE TABLE invoice_drafts` creates a new InnoDB table. On MySQL 8.0 the DDL is handled by the atomic
data dictionary; the table is empty and has no existing contract. No ALTER, no data change, and the
`KEY invoice_drafts_account_id_idx` is built on an empty table (instant).

**Evidence required:** `migration.sql`, `schema.sql` (the table does not exist yet).

**Mitigation:** none. The later FK to `accounts` should be reviewed on its own; the existing index means
adding it will not trigger an implicit index build.

### F2 — `INFO` — `no-risk-observed` — no consumers, no compatibility impact

`invoice_drafts` is not referenced by any code (`application-usage.md`), so old and new application
versions are unaffected. No FK or view depends on it. There is no data to backfill.

## Unknowns that must be reported

- The future FK and its `ON DELETE` semantics are a separate change and are not reviewed here.

## Verdict reasoning

`LOW` only, evidence present → `APPROVED`. Flagging an empty new table as a data risk is a false
positive.

## This is a negative case

Any `HIGH`+ finding, a backfill recommendation, or a non-`APPROVED` verdict counts as a false positive
/ verdict miss.
