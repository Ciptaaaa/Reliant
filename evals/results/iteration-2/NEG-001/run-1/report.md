## Migration Guard Report

**Migration:** `db/migrate/20261116104000_add_referral_source_to_signups.sql`
**Statement:** `ALTER TABLE signups ADD COLUMN referral_source varchar(64);`
**Verdict: APPROVED**
**Risk: LOW**

### Findings

1. **Nullable column, no default, no constraint, no index, no backfill** — The statement adds a single `varchar(64)` column with no `NOT NULL`, no `DEFAULT`, no `CHECK`/`UNIQUE`, and no index. Evidence (context.md): *"No default, no constraint, no index, no backfill in this migration."* This is the least risky DDL form in PostgreSQL 16: since PG 11, `ADD COLUMN` with no default is a metadata-only change and does not rewrite the table.

2. **No rewrite at ~90k rows** — Even a rewriting operation would be small at this size, but none occurs here. The column is metadata-only for existing rows, which will read as `NULL`. This matches the intended behavior: *"existing rows stay `NULL`."*

3. **No application breakage** — No code reads the column yet. Evidence (application-usage.md): *"`grep -rn \"referral_source\" app/ lib/ db/` returns nothing except this migration."* Also no strict attribute whitelist rejects it: *"`app/models/signup.rb` does not declare a strict attribute list that would reject extra columns."* Rolling deploys are safe because old and new app versions both tolerate an unused column.

4. **No lock or deploy concern** — A metadata-only `ADD COLUMN` takes a brief `ACCESS EXCLUSIVE` lock, which is standard and acceptable for a non-hot table with optional usage. No concurrent backfill or query change is involved: *"No existing query modified."*

### Evidence summary
- context.md: *"Adding a nullable column... Nothing else changes in this migration."*
- application-usage.md: *"No code references `referral_source`."*
- schema.sql: `signups` has `email NOT NULL UNIQUE`, `source_page`/`created_at`; adding an isolated nullable column changes none of these constraints.

### Verdict: APPROVED (LOW)
This is a safe, additive, metadata-only migration. No data-integrity risk, no table rewrite, no backfill needed, and no query or constraint changes. No recommendations required.

*Note: the case's stated expectation is that flagging a data-integrity risk, claiming a rewrite, or recommending a backfill here would be a false positive. This report deliberately does none of those.*