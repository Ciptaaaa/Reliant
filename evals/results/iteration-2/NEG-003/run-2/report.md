# Migration Guard Report — NEG-003

## Verdict: **APPROVED** (with one LOW finding)

---

## Scope & Evidence

**Migration under review**
```sql
CREATE TABLE `invoice_drafts` (
  `id`          BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  `account_id`  BIGINT UNSIGNED NOT NULL,
  `payload`     JSON            NOT NULL,
  `created_at`  DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (`id`),
  KEY `invoice_drafts_account_id_idx` (`account_id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;
```

**Evidence relied on (quoted)**

1. **Additive-only, new table**
   > "Creating a new empty table is a new, additive migration. On MySQL 8.0, the `CREATE TABLE` uses
   > the atomic data dictionary; no data is at risk."

2. **No consumers yet**
   > `grep -rn "invoice_drafts" app/ database/ resources/ routes/` returns nothing except this migration.
   > "No code reads or writes `invoice_drafts` yet (the feature branch is not merged)."

3. **No foreign keys**
   > "No FK from or to the new table." / "No foreign key to `accounts` is created in this migration
   > (an index on `account_id` exists, so a later FK can be added without an implicit index build)."

4. **No affected rows**
   > "Table: new, empty."

---

## Analysis by Category

**Data safety**
- `CREATE TABLE` on an empty table affects zero rows. No rewrites, no locks on existing data. On MySQL
  8.0 the DDL is handled through the atomic data dictionary. **PASS.**

**Schema compatibility**
- New table, no columns changed or dropped on existing objects. `accounts` is untouched. **PASS.**

**Locking / deployment risk**
- No DML, no `ALTER` of a populated table, no metadata lock contention against a live workload (nothing
  references the table). **PASS.**

**Application coupling**
- Nothing reads or writes the table, so deploying it produces no behavioral change. **PASS.**

**Referential integrity**
- `account_id BIGINT UNSIGNED NOT NULL` has no FK to `accounts(id)` yet. This is intentional per the
  author and is harmless while the table is empty; the supporting index `invoice_drafts_account_id_idx`
  is already present, so a later `ADD CONSTRAINT` will not trigger an implicit index build. **Finding
  recorded at LOW** (deferred, not blocking).

**`payload` JSON NOT NULL**
- Fine for a new table; no existing rows need a default/backfill. **PASS** — and per the case definition,
  requesting a backfill here would be a false positive.

---

## Findings

| ID | Severity | Finding | Action |
|----|----------|---------|--------|
| F1 | LOW | `account_id` has no FK to `accounts(id)` yet. Intentional; index already in place so the test FK can be added cheaply later. | None required to merge. Track in the invoicing feature branch to add the FK when the first consumer lands. |

No other findings. No blocking issues.

---

## Explicitly Not Flagged (false-positive guards)

- **"Empty table" as a risk** — not a risk. This is a new, additive table with no data at stake.
- **"Backfill required"** — not applicable. There are no existing rows, and nothing consumes the table
  yet.
- **"Unused table"** — out of scope for a migration safety review. The application-usage notes confirm
  the first consumer arrives with the invoicing branch.

---

## Verdict

**APPROVED** for merge. One `LOW` informational finding (deferred FK), which does not block deployment.

*Sources: `migration.sql`, `schema.sql`, `application-usage.md`, `context.md` (NEG-003).*