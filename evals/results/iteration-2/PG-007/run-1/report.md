## Migration Guard Report — PG-007

**Verdict: APPROVED** (finding severity: `LOW`)

### Migration under review
```sql
-- db/migrate/20261116103000_add_locale_to_users.sql
ALTER TABLE users ADD COLUMN locale varchar(10);
```

### Analysis

1. **Lock type and duration.** This is a single catalog-changing `ALTER TABLE`. It takes an `ACCESS EXCLUSIVE` lock on `users`, but because the change is metadata-only the lock is held only for the catalog update, not for a table rewrite. Evidence: `context.md` ("No default value"), and `migration.sql` — nullable column with no `DEFAULT` clause. On PostgreSQL 11+ adding a nullable column with no default does not rewrite the table (PG 11 release notes: fast — non-defaulted column addition, `ALTER TABLE ... ADD COLUMN ... (NULL)`).

2. **Table rewrite risk.** None. There is no `DEFAULT`, no `NOT NULL`, no type change, no backfill. Evidence: `migration.sql` has only `ADD COLUMN locale varchar(10);`.

3. **Table size impact.** `schema.sql` — "Rows: ~310,000." Even if a rewrite were triggered (it isn't), this is well below the size where rewrites become operationally hazardous. The rewrite concern is moot here anyway.

4. **Application compatibility.** No code reads or writes `locale` today. Evidence: `application-usage.md` — "Existing code references: none... `grep -r "locale" app/ lib/ config/` returns only the i18n configuration, unrelated to the users table"; and "`app/models/user.rb` declares a strict attribute list (no `locale`)". A new nullable column is invisible to code that does not select it. The later release that reads it ("absent values fall back to `en`", `docs/rfc/0042-localisation.md:12`) is forward-compatible with existing rows being `NULL`.

5. **Rolling deploy.** Safe. The column is nullable and unused by current code; both old and new application versions function correctly against the migrated schema. Evidence: `context.md` — "Application writes will start using `locale` in a later release; this migration does not touch existing columns."

6. **Stated "not searched" scope.** `application-usage.md` notes mobile clients were not searched, with the justification that "the API does not expose `locale` until the later release." This is consistent with the planned-usage statement and does not change the verdict, since nothing today can observe the column.

### Finding

| Severity | Finding |
|----------|---------|
| `LOW`    | None required. Optional follow-up: since existing rows will be `NULL`, confirm the later release's `en` fallback is enforced in code (per RFC 0042), not via a `DEFAULT`. |

### Verdict

**APPROVED.** Adding a nullable column with no default on PostgreSQL 15 is a metadata-only change: no table rewrite, sub-millisecond execution, and the `ACCESS EXCLUSIVE` lock is held only for the catalog update. Safe to deploy during business hours. Current application code does not reference `locale`, and the later release is planned against `NULL` values with an `en` fallback.