## Migration Guard Report

**Migration:** `db/migrate/20261116104000_add_referral_source_to_signups.sql`
**Engine:** PostgreSQL 16
**Table:** `signups` (~90,000 rows)

### Verdict: **APPROVED**

### Findings

| # | Severity | Finding |
|---|----------|---------|
| 1 | LOW | Additive nullable `varchar(64)` column with no default, constraint, index, or backfill. |

### Evidence

**1. The operation is purely additive and nullable.**
> `ALTER TABLE signups ADD COLUMN referral_source varchar(64);`

No `NOT NULL`, no `DEFAULT`, no `CHECK`, no `UNIQUE`, no index. In PostgreSQL 11+ (and this is PG 16), adding a nullable column with no default is a metadata-only operation — it does not rewrite the table and does not scan existing rows. The existing ~90,000 rows will simply report `NULL` for the new column via the missing value.

**2. No data-integrity risk introduced.**
No constraint is being added, and existing rows are intentionally left `NULL`:
> "Planned optional field on the signup form in a later release; existing rows stay `NULL`."

There is nothing to backfill — `NULL` is the intended state for both existing and pre-feature rows. A backfill would be unnecessary work, not a requirement.

**3. No application or query impact.**
> "No code references `referral_source`... `grep -rn \"referral_source\" app/ lib/ db/` returns nothing except this migration."

And:
> "`app/models/signup.rb` does not declare a strict attribute list that would reject extra columns."

So no code path can be broken by the new column, and no ORM strictness will reject it.

**4. Rolling deploy is safe.**
Because the column is nullable with no default and unused by code, old application instances and new instances can coexist during the rolling deploy without coordination. Writes from unaware instances will leave the column `NULL`, which is acceptable per the stated plan.

**5. Lock profile is minimal.**
A metadata-only `ADD COLUMN` takes a brief `ACCESS EXCLUSIVE` lock on `signups` solely to update the catalog; it does not hold for the duration of a rewrite (there is none). At 90k rows this is imperceptible. No long-running lock concern.

### Explicit non-findings (checked and dismissed)

- **Table rewrite:** Not applicable. No `DEFAULT` value is attached, so PG 16 does not need to rewrite the table.
- **Backfill required:** Not applicable. `NULL` is the target state; there is no derived or required value to populate.
- **Data-integrity risk:** None. No constraint, no `NOT NULL`, nothing that can be violated by existing rows.
- **Index/lock contention:** No index created; no concurrent index build needed.

### Recommendation

Approve as-is. The migration does exactly what the description says and nothing more. The LOW finding is informational: a nullable, default-less column add on a moderately sized table with no application coupling.

*(Note: If a later migration adds `NOT NULL`, a `DEFAULT`, or a `CHECK` on this column, or begins reading it in queries that assume non-null values, that follow-up should be reviewed separately — this verdict covers only the provided migration.)*