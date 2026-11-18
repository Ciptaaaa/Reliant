## Migration Guard Report — PG-007

**Migration**: `db/migrate/20261116103000_add_locale_to_users.sql`
**Statement**: `ALTER TABLE users ADD COLUMN locale varchar(10);`
**Verdict**: **APPROVED**
**Finding severity**: **LOW**

---

### 1. Operation summary

Single additive DDL: one nullable column added to `users` with no `DEFAULT` and no `NOT NULL`. Nothing else is altered, backfilled, indexed, or constrained.

### 2. Risk analysis

**Lock**: `ACCESS EXCLUSIVE` on `users`, but held only for the catalog update — new `ALTER TABLE ... ADD COLUMN` with no default is **metadata-only**.

**Table rewrite**: none.

**Row scan / backfill**: none. Existing rows stay `NULL`; PostgreSQL stores "no value" in the tuple header (null bitmap), it does not write the column value for any of the ~310k existing rows.

**Traffic impact**: sub-millisecond lock. Even under moderate traffic this is a blip, not a stall. Business-hours deployment is fine.

**PostgreSQL version gate**: PostgreSQL 11+ (here 15) added the fast-path for `ADD COLUMN` with a *constant* default; this case has no default at all, so it skips that machinery entirely and is purely catalog change. Either way no rewrite.

### 3. Evidence

- **Case context**: "adding a nullable column with no default is metadata-only: no table rewrite, sub-millisecond, and the `ACCESS EXCLUSIVE` lock is held only for the catalog update."
- **Migration**: column `locale varchar(10)` — no `DEFAULT`, no `NOT NULL`.
- **Schema**: `users` has `id … PRIMARY KEY` and no prior `locale`; adding a new column at the end of the row stores it as null in the tuple header.
- **Application usage**: "Existing code references: none." / "`app/models/user.rb` declares a strict attribute list (no `locale`)" / "`grep -r "locale" …` returns only the i18n configuration, unrelated to the users table."
- **Deployment**: "Application writes will start using `locale` in a later release; this migration does not touch existing columns."
- **Deployment**: "No default value."

### 4. Findings

| Severity | Finding | Basis |
|---|---|---|
| LOW | Business-hours deploy is safe. Catalog-only `ALTER TABLE`; no rewrite, no backfill, lock held only for catalog update. | Context + migration evidence above. |

No `MEDIUM` or higher findings. Any claim that this rewrites the table or holds a long lock would be unsupported by the evidence.

### 5. Conditions / caveats (informational, not blocking)

1. **Later release is where risk lives.** When the application begins to read/write `locale`, that release's code choice (read-only fallback to `en` vs. writes) determines behaviour. The RFC reference (`docs/rfc/0042-localisation.md:12`) covers read-side default; no migration action required here.
2. **Mobile clients**: explicitly "not affected: the API does not expose `locale` until the later release."
3. **Strict attribute list** (`app/models/user.rb`): until the later release runs, nothing selects `locale`; the column is inert. No impact on this migration's verdict.

---

**Decision**: APPROVED for merge. The single `LOW` finding is the business-hours scheduling note only — no remediation required.