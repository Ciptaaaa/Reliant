# Migration Guard Report

## Scope
- **Migration**: `database/migrations/2026_11_16_130000_accounts_add_nickname.php`
- **Engine**: MySQL 8.0.36 (InnoDB)
- **Inspected**: `migration.sql`, `schema.sql`, `application-usage.md`, `context.md`
- **Not inspected**: mobile clients (the API does not expose `nickname` until a later release)
- **Unknowns**: none material; the instant path applies to this table shape and version

## Verdict
**APPROVED** — appending a nullable column with `ALGORITHM=INSTANT` is a genuine metadata-only change on MySQL 8.0.36, and no code references the column yet.

> APPROVED means no blocker was found in the inspected scope with the evidence available. This is not a
> production guarantee.

## Findings

### F1 — LOW — operational
- **Statement**: `ALTER TABLE accounts ADD COLUMN nickname VARCHAR(80) DEFAULT NULL, ALGORITHM=INSTANT, LOCK=DEFAULT` in `migration.sql:2-3` — appends one nullable column with no default value
- **Evidence**:
  - `migration.sql:2-3` — the column is appended, `DEFAULT NULL`, with `ALGORITHM=INSTANT, LOCK=DEFAULT`
  - `schema.sql:15` — `accounts` has no generated columns and is not partitioned, so the instant path is not blocked by table shape
  - `context.md:14` — the new column is at the end of the table with no default value, the case in which `INSTANT` is genuinely supported on 8.0.12+
- **Impact**: metadata-only on this version: the catalog gains a column and existing rows read `NULL`, with the metadata lock held for a fraction of a second
- **Mitigation**: none required; optionally set `lock_wait_timeout` in the migration session so a busy moment fails fast instead of waiting
- **Verification**: `SHOW WARNINGS` after the `ALTER` reports no algorithm escalation, and `SHOW COLUMNS FROM accounts LIKE 'nickname'` shows `YES` in `Null`
- **Approval**: none
- **Reversible**: yes

### F2 — INFO — no-risk-observed
- **Statement**: checked whether any code reads or writes `nickname` before the later release
- **Evidence**:
  - `application-usage.md:3` — `grep -rn "nickname" app/ database/ resources/` returns no results
  - `application-usage.md:4` — the later release falls back to `display_name` when `nickname` is `NULL`
- **Impact**: none. Old and new application versions both tolerate the unused column, and no data is rewritten.
- **Mitigation**: none
- **Verification**: the same grep stays clean in the deployed release until the planned release ships
- **Approval**: none
- **Reversible**: yes

## Safer migration path
Not needed. This migration is already the additive, metadata-only form, and it requests the algorithm that matches the operation.

## Rollback and recovery
- **Schema rollback**: `ALTER TABLE accounts DROP COLUMN nickname` — instant on 8.0.29+; on earlier 8.0 patch versions a drop is an INPLACE rebuild, so check the patch version before relying on it being free.
- **Data recovery**: not applicable while nothing writes to the column; once the later release writes nicknames, a drop would lose them and must be re-reviewed at that point.
- **Application rollback**: safe. Old and new versions ignore the column.

## Pre-deploy checklist
- [ ] `SHOW WARNINGS` confirms the instant path on the target patch version
- [ ] Confirm no long-running transaction is open on `accounts`
- [ ] Confirm the release that will read `nickname` is a later, separate deploy