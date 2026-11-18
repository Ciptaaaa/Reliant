# Migration Guard Report

## Scope
- **Migration**: `database/migrations/2026_11_16_122000_customers_reference_unique.php`
- **Engine**: MySQL 8.0.36 (InnoDB)
- **Inspected**: `migration.sql`, `schema.sql`, `application-usage.md`, `context.md`
- **Not inspected**: the partner CSV (external, regenerated weekly)
- **Unknowns**: how many duplicate groups remain after the 2026-10-26 dedupe run; `context.md` says it left some duplicates but gives no count

## Verdict
**BLOCKED** — 3,067 duplicate groups exist under the case-insensitive collation, so the unique index cannot be built.

## Findings

### F1 — BLOCKER — unique-constraint
- **Statement**: `ALTER TABLE customers ADD UNIQUE KEY customers_reference_code_unique (reference_code)` in `migration.sql:2-3` — builds a unique index, which fails if any duplicate key exists
- **Evidence**:
  - `schema.sql:21` — `GROUP BY reference_code HAVING COUNT(*) > 1 -> 2,918` exact duplicates
  - `schema.sql:22` — `GROUP BY UPPER(reference_code) HAVING COUNT(*) > 1 -> 3,067` case-insensitive duplicates, matching the `utf8mb4_0900_ai_ci` collation at `schema.sql:12`
  - `migration.sql:2-3` — the index is added without a dedupe step
- **Impact**: the build fails with `ERROR 1062: Duplicate entry ... for key 'customers_reference_code_unique'`, the DDL is rejected and the index is not created; no row data is modified by this statement
- **Mitigation**: dedupe first, then build; the dedupe must compare under the column's collation, making the relevant set 3,067 groups, not 2,918
- **Verification**: `SELECT COUNT(*) FROM (SELECT UPPER(reference_code) FROM customers GROUP BY UPPER(reference_code) HAVING COUNT(*) > 1) d` returns 0
- **Approval**: none
- **Reversible**: yes

### F2 — HIGH — index-build
- **Statement**: `ALGORITHM=INPLACE` is declared, but a unique index build may not accept the `LOCK=NONE`-class concurrency the developer assumed, and the statement declares `LOCK=DEFAULT`
- **Evidence**:
  - `migration.sql:2-3` — `ALGORITHM=INPLACE, LOCK=DEFAULT`
  - `context.md:14` — "`LOCK=NONE` may not even be permitted for a unique index build"
  - `schema.sql:16` — 618,904 rows
- **Impact**: if MySQL escalates the algorithm or lock level, the build blocks writes for its duration on a 620k-row table, which is not the online behaviour the developer expects
- **Mitigation**: verify the accepted combination on a staging copy with `SHOW WARNINGS` after the DDL, and schedule the build in a window; the data must be clean first, so this is the second step
- **Verification**: `SHOW WARNINGS` reports no algorithm or lock escalation, and the measured lock wait on `customers` is within the agreed bound
- **Approval**: schedule
- **Reversible**: yes

### F3 — MEDIUM — application-compatibility
- **Statement**: an active import path still writes lowercase reference codes, so duplicates reappear after any one-time dedupe
- **Evidence**:
  - `application-usage.md:6` — `app/Console/Commands/ImportCustomers.php:44` "preserves the case from the CSV, which is sometimes lowercase"
  - `application-usage.md:5` — `app/Services/Customers/GenerateReference.php:18` writes uppercase, so only the importer is the gap
  - `application-usage.md:8` — `app/Http/Controllers/CustomerController.php:31` looks up with `where('reference_code', $input)`, already case-insensitive under this collation
- **Impact**: a dedupe followed immediately by the index races the weekly import, and the next import can fail the whole batch on the unique key
- **Mitigation**: normalise to uppercase on import, matching the generator, and deploy that before the index; run the dedupe after the importer fix
- **Verification**: the import runs once against a database that already has the unique index without `ERROR 1062`
- **Approval**: none
- **Reversible**: yes

## Safer migration path
1. Fix the write path: `app/Console/Commands/ImportCustomers.php:44` upper-cases `reference_code`, matching `GenerateReference.php:18`. Deploy it.
2. Confirm no new lowercase rows are written for one import cycle.
3. Dedupe under the collation, keeping the lowest `id` per group and logging every deleted row, using the existing `DedupeCustomers.php:27` logic.
4. Verify the duplicate count is 0.
5. Build the index in a window and check what MySQL actually did:
   ```sql
   ALTER TABLE customers ADD UNIQUE KEY customers_reference_code_unique (reference_code), ALGORITHM=INPLACE, LOCK=DEFAULT;
   SHOW WARNINGS;
   ```
6. Verify: `SHOW INDEX FROM customers WHERE Key_name = 'customers_reference_code_unique'`.

## Rollback and recovery
- **Schema rollback**: `ALTER TABLE customers DROP INDEX customers_reference_code_unique;` is an online operation for a secondary index.
- **Data recovery**: the dedupe deletes rows and that deletion is irreversible without a backup; export every duplicate row before deleting it.
- **Application rollback**: safe — the old application version does not depend on the index; the importer fix should stay regardless, because it is a correctness fix on its own.

## Pre-deploy checklist
- [ ] Importer normalises case and is deployed
- [ ] Duplicate count under `UPPER(reference_code)` is 0
- [ ] Duplicate rows exported before deletion
- [ ] Index built in a window, with `SHOW WARNINGS` checked
- [ ] Re-run of the weekly import verified against the new index