# Migration Guard Report — NEG-001

## Verdict: **APPROVED** (finding severity: `LOW`)

## Migration under review
```sql
-- db/migrate/20261116104000_add_referral_source_to_signups.sql
ALTER TABLE signups ADD COLUMN referral_source varchar(64);
```

## Analysis

**Statement type:** Single `ADD COLUMN`, nullable, no default.

- **Locking / rewrite risk:** In PostgreSQL 16, `ALTER TABLE ... ADD COLUMN` without a `DEFAULT` and without `NOT NULL` is a metadata-only change. It takes a brief `ACCESS EXCLUSIVE` lock and does not rewrite the table. Evidence: the migration contains only `ADD COLUMN referral_source varchar(64);` — no `DEFAULT`, no `NOT NULL`, no `UPDATE`. Context confirms: *"No default, no constraint, no index, no backfill in this migration."*
- **Table size:** ~90k rows (`Rows: ~90,000`). Even if a rewrite were involved, this is small; here none is involved.
- **Application compatibility:** No code references the column. Evidence — *"`grep -rn \"referral_source\" app/ lib/ db/` returns nothing except this migration."* Rolling deploy is safe in both directions: old code ignores the new nullable column; new code would only read it once the feature ships. *"`app/models/signup.rb` does not declare a strict attribute list that would reject extra columns."*
- **Data integrity:** Existing rows stay `NULL`, which matches the stated intent — *"Planned optional field on the signup form in a later release; existing rows stay `NULL`."* No backfill is required or recommended; a NULL default is the correct state for rows that predate the field.
- **Query impact:** None. *"No existing query modified."*

## Finding

| ID | Severity | Description |
|----|----------|-------------|
| NEG-001-F1 | `LOW` | Informational only: the new column is nullable and unindexed. No action required for this migration. |

**No data-integrity risk, no rewrite, and no backfill needed.** Any such recommendation would be a false positive for this case.

## Recommendation
Merge as-is. No changes to the migration are required.