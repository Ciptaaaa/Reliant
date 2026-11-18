# Migration Guard Report — MY-007

## Migration
`database/migrations/2026_11_16_130000_accounts_add_nickname.php` (raw SQL form)

```sql
ALTER TABLE `accounts`
  ADD COLUMN `nickname` VARCHAR(80) DEFAULT NULL,
  ALGORITHM=INSTANT, LOCK=DEFAULT;
```

## Target
- Engine: MySQL 8.0.36, InnoDB (from `schema.sql`: "MySQL 8.0.36, InnoDB")
- Table: `accounts`, "~2,400,000" rows ("No generated columns, no partitioned table.")

## Findings

### 1. INSTANT DDL applicability — INFO / LOW
**Evidence:** The migration explicitly requests `ALGORITHM=INSTANT`. The deployment facts state: "The new column is at the **end** of the table and has no default value (`DEFAULT NULL`)." Target version is 8.0.36, which is ≥ 8.0.12.
**Assessment:** On MySQL 8.0.12+, appending a column with a NULL default at the end of the table is an instant operation — metadata-only, no table rebuild, no row rewrite. The requested algorithm is genuinely applicable here (not merely requested). This satisfies the case's stated requirement to distinguish "requested" from "applicable": it is applicable. No lock escalation, no copy phase expected on a 2.4M-row table.

### 2. Nullability and application safety — INFO
**Evidence:** `application-usage.md`: "**Existing references**: none. `grep -rn "nickname" app/ database/ resources/` returns no results." Also: "No application code reads or writes `nickname` yet" (`context.md`).
**Assessment:** Adding a nullable column that nothing reads or writes is harmless under a rolling deploy. The migration-before-tasks ordering cannot break old or new code because neither version references the column.

### 3. Future consumer contract — INFO (non-blocking)
**Evidence:** Planned fallback: "a later release will read `nickname` in the profile header and fall back to `display_name` when it is `NULL`."
**Assessment:** The nullable design matches the documented fallback semantics. This is a forward-looking note only; it does not affect this migration.

## Not applicable
- No lock/rewrite risk to flag: INSTANT is applicable as described, so a rebuild warning here would be a false positive.
- Mobile clients: documented as unaffected ("API does not expose `nickname` until the later release") — consistent with the no-references evidence.

## Verdict: **APPROVED** (LOW)

All evidence supports merging: version 8.0.36 supports INSTANT append-of-nullable-column, the column has no non-null default, and no code references it. The single LOW-tier item is the informational observation that INSTANT applicability was correctly established rather than merely assumed.