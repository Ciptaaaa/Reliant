## Migration Guard Report — PG-007

**Migration file:** `db/migrate/20261116103000_add_locale_to_users.sql`
**Operation:** `ALTER TABLE users ADD COLUMN locale varchar(10);`

---

### Verdict: **APPROVED**

---

### Findings

| Severity | Finding |
|----------|---------|
| **LOW** | Nullable column, no default, no backfill — metadata-only change on PostgreSQL 15. |

---

### Evidence

**1. Nullable, no default — catalog-only on PostgreSQL 11+**

> `ALTER TABLE users ADD COLUMN locale varchar(10);`

No `NOT NULL` constraint and no `DEFAULT` clause, so PostgreSQL 11+ stores the absence of a value in the catalog rather than rewriting tuples. For each existing row, `locale` simply materializes as `NULL`. No table rewrite, no per-row work.

**2. Table size is irrelevant to lock duration**

> `-- Rows: ~310,000.` (schema.sql)

Because the operation does not rewrite or scan the table, the ~310k row count does not extend the lock. The `ACCESS EXCLUSIVE` lock is held only long enough to update the system catalogs (sub-millisecond). This matters for the rolling deploy:

> **Application**: Rails 7 API, rolling deploy (context.md)

A sub-millisecond `ACCESS EXCLUSIVE` lock will not meaningfully block concurrent `users` traffic.

**3. No existing code depends on the column shape**

> **Existing code references**: none. … no code path reads or writes `locale`  (application-usage.md)
> `app/models/user.rb` declares a strict attribute list (no `locale`); until the later release runs, nothing selects it.

Nothing in the running application selects or writes `locale`, and the strict attribute list in `User` is not broken by the new column. The migration is forward-safe under a rolling deploy: old code ignores the column, and new code is deferred to a later release.

> Application writes will start using `locale` in a later release; this migration does not touch existing columns. (context.md)

**4. No default to worry about**

> No default value. (context.md)

This confirms the metadata-only path. (A `VOLATILE` default would have forced a rewrite on PG ≤ 10 / a non-volatile default on PG 11+, but neither applies here.)

---

### Notes (non-blocking)

- The later write path must handle `NULL` **and** the varchar(10) cap; both are product/validation concerns, not migration-safety concerns.
- If a later release adds `NOT NULL` / `DEFAULT`, that is a **separate** migration and should be reviewed on its own merits.

---

### Summary

Adding a nullable `varchar(10)` column with no default on PostgreSQL 15 is a metadata-only operation. It is instant, holds the `ACCESS EXCLUSIVE` lock only for the catalog update, and is safe during business hours under a rolling deploy. **APPROVED** with a single **LOW** finding. No rewrite, no backfill, no lock risk.