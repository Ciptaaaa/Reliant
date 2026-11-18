# Migration Guard Report

## Scope
- **Migration**: `db/migrate/20261116140000_remove_fax_from_customers.sql`
- **Engine**: PostgreSQL 16
- **Inspected**: `migration.sql`, `schema.sql`, `application-usage.md`, `context.md`
- **Not inspected**: the partner invoicing service (separate repository), the BI warehouse
- **Unknowns**: whether the partner invoicing service reads `fax` from the API — it could hide a second `BLOCKER`

## Verdict
**BLOCKED** — `fax` is still read by `customers_controller.rb:44`, so dropping it breaks the invoice letterhead on deploy.

## Findings

### F1 — BLOCKER — destructive-column-drop
- **Statement**: `ALTER TABLE customers DROP COLUMN fax` in `migration.sql:2` — removes the `fax` column and its ~9,400 non-NULL values
- **Evidence**:
  - `application-usage.md` — `app/controllers/customers_controller.rb:44` reads `customer.fax` when rendering the invoice letterhead
  - `migration.sql:2` — the statement drops the column unconditionally
  - `schema.sql` — `fax varchar(32)` with ~9,400 non-NULL values, so the drop removes live data, not a placeholder
- **Impact**: any customer invoice rendered after the migration raises `PG::UndefinedColumn`; the ~9,400 fax values are deleted and cannot be restored by re-adding the column
- **Mitigation**: retire every `fax` reader first (controller, any partner contract), verify with a repository-wide search, then drop in a separate release. Until then, expand-and-contract: ship the code change, observe, then drop.
- **Verification**: `grep -rn "fax" app/ lib/ config/` returns no reader; the nightly invoice job completes twice without error after the code deploy
- **Approval**: accept-risk
- **Reversible**: no

### F2 — HIGH — data-integrity
- **Statement**: the same `DROP COLUMN` also destroys the ~9,400 stored values
- **Evidence**:
  - `migration.sql:2` — the `DROP COLUMN` that removes the stored values
  - `schema.sql:4` — `fax varchar(32)`; ~9,400 of ~120,000 rows carry a non-NULL value
  - `context.md` — the only restore path is the nightly `pg_dump` with a 24h RPO
- **Impact**: values updated in the 24 hours before detection are permanently lost; a restore rolls back unrelated changes since the dump
- **Mitigation**: take a `pg_dump` of `customers` immediately before the drop and store it with a named retention period; record the restore point
- **Verification**: the dump exists, restores into a scratch database, and `SELECT count(*) FROM customers WHERE fax IS NOT NULL` returns ~9,400 there
- **Approval**: accept-risk
- **Reversible**: no

## Safer migration path
1. Deploy the code change that stops reading `fax` (`customers_controller.rb:44`, plus any partner contract) and confirm the old release is drained.
2. Wait one full deploy cycle. Watch the invoice job and the error rate.
3. Take a named `pg_dump` of `customers`; record the restore point beside the migration.
4. Run the drop alone, in its own migration, with a human approval attached:
   `ALTER TABLE customers DROP COLUMN fax;`
5. Verify: `\d customers` no longer lists `fax`, and the invoice job has completed twice.

## Rollback and recovery
- **Schema rollback**: `ALTER TABLE customers ADD COLUMN fax varchar(32);` restores the structure quickly, but the column comes back empty.
- **Data recovery**: only from the named dump taken in step 3; this is why the dump is a precondition, not a formality.
- **Application rollback**: safe before step 4 (the code tolerates the column); after step 4, rolling the application back breaks the invoice letterhead again.

## Pre-deploy checklist
- [ ] Human approval recorded for the `accept-risk` drop
- [ ] Named `customers` dump taken and restore-tested
- [ ] Repository-wide search for `fax` is clean, and the partner contract is confirmed
- [ ] Old application release fully drained
