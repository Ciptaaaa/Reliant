## Migration Guard Report — NEG-003

**Migration:** `database/migrations/2026_11_16_133000_create_invoice_drafts.php` (CREATE TABLE `invoice_drafts`)
**Engine:** MySQL 8.0.36, InnoDB
**Verdict:** ✅ **APPROVED**

### Findings

| # | Severity | Finding | Evidence |
|---|----------|---------|----------|
| 1 | **LOW** | New, additive table creation with no existing consumers. No data at risk. | See evidence below. |

### Evidence

**Additive only — no existing object is altered or dropped.** The migration contains a single statement, `CREATE TABLE \`invoice_drafts\` (...)`, against a table that is not in the current schema (`schema.sql` lists only `accounts`). Nothing in the migration touches `accounts`.

**No data at risk — table is empty at deploy time.** Per `context.md`: *"Table: new, empty."* Per `application-usage.md`: *"No references: `grep -rn "invoice_drafts" app/ database/ resources/ routes/` returns nothing except this migration."* There is nothing to backfill, lock, or migrate.

**No foreign keys, so no validation scan or implicit index build.** From `application-usage.md`: *"No foreign key to `accounts` is created in this migration (an index on `account_id` exists, so a later FK can be added without an implicit index build)."* From `context.md`: *"No FK from or to the new table."* The explicit `KEY \`invoice_drafts_account_id_idx\` (\`account_id\`)` is good pre-work: it means a future FK from a merged feature branch will not trigger an implicit index creation.

**MySQL 8.0 atomic DDL.** Per `context.md`: *"On MySQL 8.0, the `CREATE TABLE` uses the atomic data dictionary; no data is at risk."* A single-statement `CREATE TABLE` is atomic; there is no partial-failure window and no metadata-lock cascade on populated tables.

**Schema shape is unremarkable.** `id BIGINT UNSIGNED` auto-increment primary key, `account_id BIGINT UNSIGNED NOT NULL`, `payload JSON NOT NULL`, `created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP`, utf8mb4/utf8mb4_0900_ai_ci matching `accounts`. No problematic defaults (no zero-dates, no functions on `NOT NULL` columns beyond `CURRENT_TIMESTAMP`, which is legal on MySQL 8), no implicit row-format or charset change.

### Why this is not a false positive

- **Not flagged as "empty table risk."** An empty table is not a migration risk; there is no data to lose, rewrite, or backfill.
- **No backfill requested.** Backfills apply only when a column is added to an existing populated table or becomes `NOT NULL` on existing rows. Neither applies.
- **The `NOT NULL` foreign-key column is not a hazard here.** `account_id BIGINT UNSIGNED NOT NULL` has no `REFERENCES` clause, so MySQL does not scan `accounts` or take shared locks on it. The column's `NOT NULL`-ness will matter only when the feature branch inserts rows, and that is out of scope for this migration.
- **The missing FK is an accepted, documented deferral.** It is covered by the pre-created index, so it can be added later without an implicit index build on a populated table.

### Watch-items for the follow-up (not blockers)

1. **When the invoicing feature branch merges:** add the FK on `account_id` → `accounts.id`. Because `invoice_drafts_account_id_idx` already exists, this is a cheap, online, additive change.
2. **When `invoice_drafts` accumulates rows:** adding any further `NOT NULL`-without-default columns later will require a backfill; plan those before the table grows.

### Verdict

**APPROVED** — one `LOW` finding (new, additive, empty table). No blocking issues. This matches the expected outcome for NEG-003.