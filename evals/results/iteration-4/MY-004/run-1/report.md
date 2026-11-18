# Migration Guard Report

## Scope
- **Migration**: `database/migrations/2026_11_16_123000_payments_order_fk.php`
- **Engine**: MySQL 8.0.36 (InnoDB)
- **Inspected**: `migration.sql`, `schema.sql`, `application-usage.md`, `context.md`
- **Not inspected**: the gateway settlement file (external)
- **Unknowns**: how often `ArchiveOldOrders` runs relative to the deploy is not stated

## Verdict
**BLOCKED** — 47,308 payments reference an order that does not exist, so MySQL rejects the foreign key.

## Findings

### F1 — BLOCKER — foreign-key
- **Statement**: `ALTER TABLE payments ADD CONSTRAINT payments_order_id_fk FOREIGN KEY (order_id) REFERENCES orders (id)` in `migration.sql:2-3` — validates every existing `payments.order_id`
- **Evidence**:
  - `schema.sql:24` — `SELECT COUNT(*) ... LEFT JOIN orders o ON o.id = p.order_id WHERE o.id IS NULL -> 47,308` orphans
  - `schema.sql:23` — "FK status: none on payments.order_id", so the check covers all 11,204,882 payments
  - `migration.sql:2-3` — no pre-repair step; MySQL has no `NOT VALID`, so the data must already be clean
- **Impact**: MySQL rejects the statement with `ERROR 1452: Cannot add or update a child row: a foreign key constraint fails`; the DDL is not applied and no row is modified
- **Mitigation**: resolve or quarantine the 47,308 orphan payments before adding the constraint, then add it in a separate step
- **Verification**: the orphan query returns 0 immediately before the DDL runs
- **Approval**: none
- **Reversible**: yes

### F2 — HIGH — application-compatibility
- **Statement**: the constraint uses the default action, which breaks the archive path that deletes orders without deleting their payments first
- **Evidence**:
  - `application-usage.md:31` — `app/Services/Orders/ArchiveOldOrders.php:41` moves orders to `orders_archive` and deletes from `orders` **without** deleting payments
  - `application-usage.md:21` — `app/Services/Orders/DeleteOrder.php:28` deletes payments first, so that path survives
  - `migration.sql:2-3` — no `ON DELETE` clause, so the default `RESTRICT`/`NO ACTION` applies
- **Impact**: the archive job starts failing with `ERROR 1451: Cannot delete or update a parent row` for every archived order that still has payments, which is the normal case
- **Mitigation**: update `ArchiveOldOrders` to delete or reassign its payments before deleting orders, and deploy it with or before the constraint; do not rely on a cascade the code does not implement
- **Verification**: the archive job completes on a test batch of orders that have payments, without `ERROR 1451`
- **Approval**: schedule
- **Reversible**: yes

### F3 — HIGH — lock-and-rewrite
- **Statement**: adding a foreign key checks 11.2M child rows and takes a shared lock on the parent, with no staging verification
- **Evidence**:
  - `migration.sql:2-3` — a plain `ADD CONSTRAINT`, no `ALGORITHM`/`LOCK` and no staging run
  - `schema.sql:22` — `payments` holds 11,204,882 rows, and `context.md:11` gives `orders` at ~3.2M rows
- **Impact**: the validation scan can block writes on `payments` for its duration and hold an MDL on `orders`; on production-sized tables that is an outage risk, not a deploy detail
- **Mitigation**: validate on a staging copy first, run in a window, and keep `foreign_key_checks=0` out of the solution — disabling the check skips the validation that is the point of the FK
- **Verification**: staging timing recorded, and no `ERROR 1452` on a data-identical copy before production
- **Approval**: schedule
- **Reversible**: yes

## Safer migration path
1. Decide what the 47,308 orphans are: payments belonging to orders that were re-keyed in the 2021 migration (`schema.sql:25`). Map them to the new ids if a mapping exists, otherwise move them to an `orphaned_payments` archive table with a named retention.
2. Fix the archive path: `app/Services/Orders/ArchiveOldOrders.php:41` must delete or reassign payments before deleting the order. Deploy it.
3. Verify the orphan count is 0 and the archive job passes on a test batch.
4. Add the constraint in a window, after a staging run that records the duration:
   ```sql
   ALTER TABLE payments ADD CONSTRAINT payments_order_id_fk FOREIGN KEY (order_id) REFERENCES orders (id);
   ```
5. Verify: `SELECT COUNT(*) FROM information_schema.TABLE_CONSTRAINTS WHERE CONSTRAINT_NAME = 'payments_order_id_fk'` returns 1, and the archive job completes in production.

## Rollback and recovery
- **Schema rollback**: `ALTER TABLE payments DROP FOREIGN KEY payments_order_id_fk;` is immediate and clean.
- **Data recovery**: repairing orphans by moving or deleting rows is irreversible without an export; export the 47,308 rows before changing them.
- **Application rollback**: safe only if the archive-path change ships in the same release; otherwise archiving resumes failing.

## Pre-deploy checklist
- [ ] Orphan decision made and exported rows retained
- [ ] Archive path updated and deployed
- [ ] Orphan count is 0
- [ ] Staging run timed, and the production window approved
- [ ] `foreign_key_checks` left enabled during the change