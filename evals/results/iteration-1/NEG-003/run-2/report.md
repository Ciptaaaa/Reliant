# Migration Guard Report — NEG-003

## Summary
**Verdict: APPROVED**
**Finding severity: LOW**

## Migration reviewed
`database/migrations/2026_11_16_133000_create_invoice_drafts.php` (raw SQL form) — creates one new table, `invoice_drafts`, with a primary key, one secondary index (`invoice_drafts_account_id_idx` on `account_id`), and no foreign keys.

## Evidence

1. **Additive, new-table-only change.** The migration contains a single statement, `CREATE TABLE \`invoice_drafts\` (...)`, and nothing else — no `ALTER`, no `DROP`, no `UPDATE`/`DELETE`, no backfill. Existing tables are untouched.

2. **No consumers yet, so no lock or availability risk to running code.** `context.md`: *"No code reads or writes `invoice_drafts` yet (the feature branch is not merged)."* `application-usage.md`: *"grep -rn "invoice_drafts" app/ database/ resources/ routes/ returns nothing except this migration."* The table is created empty and will stay empty until the invoicing branch lands.

3. **No data at risk, and the DDL is atomic.** `context.md` (engine): *"MySQL 8.0.36, InnoDB"* and *"the `CREATE TABLE` uses the atomic data dictionary; no data is at risk."* Empty table; no rows to lose.

4. **No foreign keys, so no FK validation scan and no parent-table locking.** `context.md`: *"No FK from or to the new table."* `application-usage.md` confirms the design intent: *"No foreign key to `accounts` is created in this migration (an index on `account_id` exists, so a later FK can be added without an implicit index build)."* This is a deliberate, sound choice — the index on `account_id` is present, so adding the FK later will not trigger an implicit index build.

5. **Schema is internally consistent.** `account_id BIGINT UNSIGNED NOT NULL` matches `accounts.id BIGINT UNSIGNED` in `schema.sql`, so a future FK is type-compatible. The charset/collation (`utf8mb4` / `utf8mb4_0900_ai_ci`) matches the existing `accounts` table.

## Findings

- **LOW — Orphan-row window before FK is added.** Because no FK exists yet, `invoice_drafts.account_id` values are unchecked against `accounts`. This is harmless *now* (the table is empty and has no writers), but becomes a real integrity gap the moment the feature branch starts inserting. **Action:** add the FK on `account_id → accounts.id` in the feature-branch migration that first writes to this table (or as part of rolling out the feature). The existing index makes this cheap. Not a merge blocker.

## Explicitly not flagged (would be false positives)

- **"Empty table" is not a risk.** A new, empty table cannot cause query-plan regressions, lock contention with running code, or data loss. No consumers exist to be affected.
- **No backfill requested or needed.** There is no prior data to reconcile and no existing code path to keep consistent.
- **Forward migration only; no rollback/down-migration concern raised.** Nothing depends on the table yet, so a rollback would simply drop an empty table.

## Verdict
**APPROVED.** This is a purely additive, atomic `CREATE TABLE` on MySQL 8.0.36 with no consumers, no FK, and no data at risk. The only finding is the expected, LOW-severity note that an FK on `account_id` should be added when the invoicing feature begins writing to the table. Merge it.