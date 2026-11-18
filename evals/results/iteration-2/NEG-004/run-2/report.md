## Migration Guard Report — NEG-004

**Migration:** `db/migrate/20261116112000_add_currency_to_invoices.sql`
**Engine:** PostgreSQL 16
**Verdict:** ✅ **APPROVED** — Risk: **LOW**

---

### Change summary

```sql
ALTER TABLE invoices ADD COLUMN currency varchar(3) NOT NULL DEFAULT 'USD';
```

Adds a single new column (`varchar(3)`, `NOT NULL`) with a literal-constant default `'USD'` to an existing ~40,000-row table.

---

### Findings

| # | Area | Severity | Assessment |
|---|------|----------|------------|
| 1 | Table rewrite / lock time | LOW | **No rewrite.** Constant default is metadata-only on PG 11+. |
| 2 | Backfill requirement | LOW | **None.** Existing rows read the default from metadata. |
| 3 | Application compatibility (rolling deploy) | LOW | Safe — see evidence. |
| 4 | Index / constraint impact | LOW | **None.** No index or constraint references `currency`. |

---

### Evidence

**1. No table rewrite — version-dependent, and the version is confirmed safe.**

- Engine is confirmed as PostgreSQL 16: *"PostgreSQL 16 (confirmed in `docker-compose.yml`)."*
- The default is a literal, volatile-free constant: *"The default is a literal constant (`'USD'`), not a volatile function."*
- Therefore, on PG 11+, `ADD COLUMN ... NOT NULL DEFAULT <constant>` performs no table rewrite and holds only a brief `ACCESS EXCLUSIVE` lock for catalog update. Combined with the stated environment: *"We have already confirmed the table is ~40k rows and there are no long transactions"* — lock acquisition is trivial.

> ⚠️ Version caveat (for the record): on **PG 10 and older** this same statement *does* rewrite the table. Since the target is confirmed PG 16, that does not apply here.

**2. No backfill required.**

- Backfill task confirmed absent and unnecessary: *"`lib/tasks/backfill_invoices.rake` — does not exist; no backfill is required because the default is stored in metadata and applied to existing rows on read."*
- Existing ~40k rows will read `'USD'` from metadata with no data mutation.

**3. Rolling-deploy safety.**

- The developer's intent is *"for new rows"*, and inserts not specifying the column receive `'USD'`: *"existing serializer does not reference it and inserts do not specify it, so they receive `'USD'`."*
- Adding a nullable-with-default-then-reading-from-metadata column is additive and backward compatible. Old code paths that don't reference `currency` continue to write successfully (the default applies server-side).
- **Minor watch item (not blocking):** `app/serializers/invoice_serializer.rb:14` *"will include `currency` once merged"* — ensure the serializer change ships only after the migration is applied on all nodes, which the standard rolling-deploy ordering (migrate first) satisfies.

**4. No index/constraint coupling.**

- *"No index or constraint references `currency`."* — so no `CONCURRENTLY` concerns, no index rebuild, no dependent-object lock escalation.

---

### Recommendations

- Proceed with merge. Standard practice: run the migration before deploying application code that references `currency` (which it already is, since inserts/existing code don't depend on it).
- Optional hardening (not required for this case): if you later add an index or constraint on `currency`, use `CREATE INDEX CONCURRENTLY` and `NOT VALID` constraint patterns.

---

### Verdict

**APPROVED — Risk LOW.** The `NOT NULL` + constant default on a new column is metadata-only on the confirmed PostgreSQL 16 target: no table rewrite, no scan, no backfill. The only relevant caveat is the version dependency (PG ≤ 10 *would* rewrite), which is moot given the confirmed PG 16 environment.