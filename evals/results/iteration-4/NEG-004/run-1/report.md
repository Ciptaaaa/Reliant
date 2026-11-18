# Migration Guard Report

## Scope
- **Migration**: `db/migrate/20261116112000_add_currency_to_invoices.sql`
- **Engine**: PostgreSQL 16
- **Inspected**: `migration.sql`, `schema.sql`, `application-usage.md`, `context.md`
- **Not inspected**: the serializer branch that will expose `currency` once merged
- **Unknowns**: none material; the version and the default's volatility are stated

## Verdict
**APPROVED** — adding a new column with a non-volatile constant default is metadata-only on PostgreSQL 16, and no existing row is rewritten.

> APPROVED means no blocker was found in the inspected scope with the evidence available. This is not a
> production guarantee.

## Findings

### F1 — LOW — operational
- **Statement**: `ALTER TABLE invoices ADD COLUMN currency varchar(3) NOT NULL DEFAULT 'USD'` in `migration.sql:2` — appends a non-null column whose default is a literal constant
- **Evidence**:
  - `migration.sql:2` — `NOT NULL DEFAULT 'USD'`, a literal, not a function call
  - `schema.sql:7` — `invoices` holds ~40,000 rows
  - `context.md:13` — the default is a literal constant, not a volatile function
- **Impact**: on PostgreSQL 11+ a non-volatile default is stored in the catalog and applied on read, so there is no table rewrite and the `ACCESS EXCLUSIVE` lock covers only the catalog update. On PostgreSQL 10 and older the same statement rewrites the table.
- **Mitigation**: none required on this version; if the target were ever downgraded to PG 10 or older this becomes a rewrite and needs a window
- **Verification**: `\d invoices` shows `currency character varying(3) not null default 'USD'::character varying`, and the statement completes without a rewrite visible as a long scan
- **Approval**: none
- **Reversible**: yes

### F2 — INFO — no-risk-observed
- **Statement**: checked whether a backfill is required for the existing rows
- **Evidence**:
  - `application-usage.md:5` — `lib/tasks/backfill_invoices.rake` does not exist and no backfill is required, because the default is stored in metadata and applied on read
  - `application-usage.md:3` — `app/serializers/invoice_serializer.rb:14` does not reference the column yet, and inserts that omit it receive `'USD'`
- **Impact**: none. Existing rows read the default without being updated, and no writer needs to change.
- **Mitigation**: none — a backfill would be wasted writes on 40,000 rows and is not required
- **Verification**: `SELECT DISTINCT currency FROM invoices` returns `USD` for all existing rows
- **Approval**: none
- **Reversible**: yes

## Safer migration path
Not needed. This is already the safe form for PostgreSQL 11+.

## Rollback and recovery
- **Schema rollback**: `ALTER TABLE invoices DROP COLUMN currency;` — metadata-only again, and clean while the serializer has not shipped.
- **Data recovery**: not applicable; no existing value is changed. Once writers store real currencies a drop would lose them and must be re-reviewed at that point.
- **Application rollback**: safe. Inserts that omit the column receive `'USD'` either way.

## Pre-deploy checklist
- [ ] Confirm the target is PostgreSQL 11 or newer, so the default stays metadata-only
- [ ] Confirm no long-running transaction is open on `invoices`