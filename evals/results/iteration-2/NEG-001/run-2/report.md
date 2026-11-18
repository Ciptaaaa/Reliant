# Migration Guard Report — NEG-001

## Migration under review

`db/migrate/20261116104000_add_referral_source_to_signups.sql`

```sql
ALTER TABLE signups ADD COLUMN referral_source varchar(64);
```

## Verdict: **APPROVED** — Finding severity: **LOW**

## Findings

**1. [LOW] Nullable column addition with no default — safe on PostgreSQL 16.**

The migration adds a column with no `DEFAULT`, no `NOT NULL`, no constraint, and no index. In PostgreSQL 11+, adding a nullable column without a default is a metadata-only change (`ATTRIBUTE` update in `pg_attribute`); no table rewrite occurs, and existing rows read as `NULL`. The deployment facts confirm this: "No default, no constraint, no index, no backfill in this migration."

No lock escalation beyond the brief `ACCESS EXCLUSIVE` taken for the `ALTER TABLE` itself — this is required for any DDL and is short-lived here (catalog-only change, ~90k rows is irrelevant to its duration).

**2. Application compatibility — no consumer, no risk.**

Evidence from `application-usage.md`: "No code references `referral_source`. `grep -rn "referral_source" app/ lib/ db/` returns nothing except this migration." And "`app/models/signup.rb` does not declare a strict attribute list that would reject extra columns." A rolling deploy is therefore safe in both directions: old code ignores the new column; new code that later reads it will see `NULL` for existing rows, which is the intended behavior ("existing rows stay `NULL`", "Planned optional field ... in a later release").

## Explicitly checked and not flagged (false positives avoided)

- **Table rewrite:** not applicable — nullable add without default is catalog-only on PG 16.
- **Backfill:** none required. The spec says existing rows stay `NULL`; the field is optional and not yet consumed.
- **`NOT NULL` / constraint risk:** no `NOT NULL` clause present.
- **Index/lock risk:** no index creation, no `CONCURRENTLY` consideration needed.
- **Deploy ordering:** no code reads the column yet, so no code-before/after-schema hazard.

## Recommendation

Merge as-is. No follow-up migration, lock, or backfill required for this change.