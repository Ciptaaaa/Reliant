## Migration Guard Report — PG-007

**Verdict: APPROVED**

**Severity: LOW**

### Migration under review

```sql
-- db/migrate/20261116103000_add_locale_to_users.sql
ALTER TABLE users ADD COLUMN locale varchar(10);
```

### Findings

**1. `ALTER TABLE ... ADD COLUMN` (nullable, no default) — LOW**

- **Engine evidence**: `context.md` states "**Engine**: PostgreSQL 15".
- **No default**: `context.md` states "No default value." and `migration.sql` confirms the column has no `DEFAULT` clause.
- **Nullability**: The column is nullable (no `NOT NULL`), so existing rows are not affected. This matches `application-usage.md`: "absent values fall back to `en`."
- **Lock behavior**: On PostgreSQL 11+, adding a nullable column with no default is metadata-only: no table rewrite, sub-millisecond, and the `ACCESS EXCLUSIVE` lock is held only for the catalog update.
- **Table size**: `schema.sql` notes `users` has "~310,000" rows. Row count doesn't affect this operation, since no rewrite occurs.

**2. Application compatibility — LOW (informational)**

- `application-usage.md` states: "**Existing code references**: none… no code path reads or writes `locale`" and "`app/models/user.rb` declares a strict attribute list (no `locale`); until the later release runs, nothing selects it."
- The migration changes no existing columns, so the rolling deploy has no read/write window during which old code sees a new required field. The new column stays unused until the later release.

**3. Deployment timing — LOW**

- "Fine to deploy during business hours?" `context.md`: Yes. The `ACCESS EXCLUSIVE` lock is held only for the brief catalog update. It can still queue behind long-running transactions, but the lock itself is momentary.

### Why this is LOW, not MEDIUM

- The only risk is the ordinary `ACCESS EXCLUSIVE` lock every `ALTER TABLE` takes. On PostgreSQL 11+, a nullable column with no default does not rewrite the table.
- No backfill, no default, no `NOT NULL`, no index, and no `VALIDATE CONSTRAINT` step is required.

### Not searched (per application usage doc)

- **Mobile clients**: Not affected. The API does not expose `locale` until the later release.

### Recommendation

Approve and merge. No pre-deploy, backfill, or lock-window mitigation is needed.