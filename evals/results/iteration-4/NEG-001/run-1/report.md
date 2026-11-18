# Migration Guard Report

## Scope
- **Migration**: `db/migrate/20261116104000_add_referral_source_to_signups.sql`
- **Engine**: PostgreSQL 16
- **Inspected**: `migration.sql`, `schema.sql`, `application-usage.md`, `context.md`
- **Not inspected**: mobile clients that may submit the later signup form
- **Unknowns**: none material; the later consumer is out of scope for this migration

## Verdict
**APPROVED** — one nullable column with no default is a metadata-only change and nothing references it yet.

> APPROVED means no blocker was found in the inspected scope with the evidence available. This is not a
> production guarantee.

## Findings

### F1 — LOW — operational
- **Statement**: `ALTER TABLE signups ADD COLUMN referral_source varchar(64)` in `migration.sql:2` — appends one nullable column with no default and no constraint
- **Evidence**:
  - `migration.sql:2` — nullable, no `DEFAULT`, no index
  - `schema.sql:3` — `signups` has no generated columns and is not partitioned, so the catalog update is the whole operation
  - `context.md:1` — PostgreSQL 16, ~90,000 rows
- **Impact**: metadata-only on PostgreSQL 11+; no table rewrite, and the `ACCESS EXCLUSIVE` lock lasts for the catalog update only
- **Mitigation**: none required; optionally set `lock_timeout` in the migration session so a busy moment fails fast instead of queueing
- **Verification**: the migration applies immediately; `\d signups` lists `referral_source character varying(64)` and existing rows read `NULL`
- **Approval**: none
- **Reversible**: yes

### F2 — INFO — no-risk-observed
- **Statement**: checked whether existing code paths could be affected by the new column
- **Evidence**:
  - `application-usage.md:3` — `grep -rn "referral_source" app/ lib/ db/` returns nothing except this migration
  - `application-usage.md:5` — `app/models/signup.rb` declares no strict attribute list, so an extra column is not rejected
- **Impact**: none. Old and new application versions both tolerate an unused column, and no existing row or query is modified.
- **Mitigation**: none
- **Verification**: the same grep stays clean in the deployed release
- **Approval**: none
- **Reversible**: yes

## Safer migration path
Not needed. This migration is already the additive, metadata-only form.

## Rollback and recovery
- **Schema rollback**: `ALTER TABLE signups DROP COLUMN referral_source` — clean while nothing writes to it.
- **Data recovery**: not applicable; no existing data is changed and new rows read `NULL`.
- **Application rollback**: safe. Old and new versions ignore the column.

## Pre-deploy checklist
- [ ] Confirm no long-running transaction is open on `signups` (`pg_stat_activity`)
- [ ] Confirm the release that submits the field is a later, separate deploy