# Migration Guard Report — NEG-003

**Migration:** `database/migrations/2026_11_16_133000_create_invoice_drafts.php` (raw SQL form)
**Table:** `invoice_drafts` (new)
**Target:** MySQL 8.0.36, InnoDB / Laravel 11

---

## Verdict

**APPROVED** — risk level **LOW**.

---

## Findings

### 1. New, additive `CREATE TABLE` — no existing data at risk
- The migration's only statement is `CREATE TABLE \`invoice_drafts\` (...)`.
- Creates a brand-new relation; does not alter, drop, or backfill any existing table.
- On MySQL 8.0, `CREATE TABLE` is served by the atomic data dictionary, so a failure cannot leave a half-created table behind.

### 2. No consumers (evidence)
- `application-usage.md`: *"No references: `grep -rn "invoice_drafts" app/ database/ resources/ routes/` returns nothing except this migration."*
- *"The invoicing feature branch is not merged; when merged it will be the first consumer."*
- No reads or writes depend on this table, so there is no coordination requirement and no lock contention against application traffic.

### 3. No foreign keys — index in place for a future FK
- *"No foreign key to `accounts` is created in this migration (an index on `account_id` exists, so a later FK can be added without an implicit index build)."*
- The migration defines `` KEY `invoice_drafts_account_id_idx` (`account_id`) ``, so a future FK on `account_id` will not trigger an implicit index build.
- `context.md` confirms: *"No FK from or to the new table."*

### 4. Schema conventions
- `id BIGINT UNSIGNED AUTO_INCREMENT` PRIMARY KEY — consistent with `accounts.id`.
- `account_id BIGINT UNSIGNED NOT NULL` — type-compatible with `accounts.id` for a later FK.
- `utf8mb4` / `utf8mb4_0900_ai_ci` — same charset and collation as `accounts`.
- `payload JSON NOT NULL` and `created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP` — acceptable for an empty table.

---

## Not findings (explicitly checked, not raised)

- **"Empty table"** is not a risk. It is the expected state for a table with no consumers and requires no backfill.
- **No backfill requested.** There is no prior data to populate.
- **No access-pattern or lock concern.** No code reads or writes the table in this deployment.

---

## Risk summary

| Severity | Count |
|----------|-------|
| HIGH     | 0     |
| MEDIUM   | 0     |
| LOW      | 1     |

**LOW finding:** New unused table created ahead of its feature. Purely additive and reversible (a plain `DROP TABLE` if abandoned). Acceptable to merge.

**Verdict: APPROVED.**