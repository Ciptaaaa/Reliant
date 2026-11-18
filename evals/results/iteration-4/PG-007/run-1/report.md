# Migration Guard Report

## Scope
- **Migration**: `db/migrate/20261116103000_add_locale_to_users.sql`
- **Engine**: PostgreSQL 15
- **Inspected**: `migration.sql`, `schema.sql`, `application-usage.md`, `context.md`
- **Not inspected**: mobile clients (the API does not expose `locale` until a later release)
- **Unknowns**: none material; the later consumer is out of scope for this migration

## Verdict
**APPROVED** — a single nullable column with no default is a metadata-only change and no code references it yet.

> APPROVED means no blocker was found in the inspected scope with the evidence available. This is not a
> production guarantee.

## Findings

### F1 — LOW — operational
- **Statement**: `ALTER TABLE users ADD COLUMN locale varchar(10)` in `migration.sql:2` — appends one nullable column with no default and no constraint
- **Evidence**:
  - `migration.sql:2` — nullable, no `DEFAULT`, no index
  - `schema.sql:7` — `users` has no generated columns and is not partitioned, so the catalog update is the only work
  - `context.md:15` — no default value
- **Impact**: on PostgreSQL 11+ this is metadata-only: no table rewrite, and the `ACCESS EXCLUSIVE` lock is held only for the catalog update on 310,000 rows
- **Mitigation**: none required; optionally set `lock_timeout` in the migration session so a busy moment fails fast instead of queueing
- **Verification**: the migration applies immediately; `\d users` lists `locale character varying(10)` and existing rows read `NULL`
- **Approval**: none
- **Reversible**: yes

### F2 — INFO — no-risk-observed
- **Statement**: checked whether any code path reads or writes `locale` before the later release
- **Evidence**:
  - `application-usage.md:4` — `grep -r "locale" app/ lib/ config/` returns only the i18n configuration, unrelated to this table
  - `application-usage.md:6` — `app/models/user.rb` declares a strict attribute list that does not include `locale`
- **Impact**: none. Old and new application versions both tolerate the unused column and no existing row or query changes.
- **Mitigation**: none
- **Verification**: the same grep stays clean in the deployed release until the planned release reads the column
- **Approval**: none
- **Reversible**: yes

## Safer migration path
Not needed. This migration is already the additive, metadata-only form.

## Rollback and recovery
- **Schema rollback**: `ALTER TABLE users DROP COLUMN locale` — clean while nothing writes to it.
- **Data recovery**: not applicable; no existing data is changed and new rows read `NULL`.
- **Application rollback**: safe. Old and new versions ignore the column.

## Pre-deploy checklist
- [ ] Confirm no long-running transaction is open on `users` (`pg_stat_activity`)
- [ ] Confirm the release that will read `locale` is a later, separate deploy