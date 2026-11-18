## Migration Guard Report — NEG-001

**Migration:** `db/migrate/20261116104000_add_referral_source_to_signups.sql`
**Target:** `signups` (PostgreSQL 16, ~90,000 rows)
**Verdict:** ✅ **APPROVE** — Finding severity: **LOW**

---

### Change summary

Single statement:

```sql
ALTER TABLE signups ADD COLUMN referral_source varchar(64);
```

Adds one nullable `varchar(64)` column. No default, no `NOT NULL`, no constraint, no index, no backfill, no other statement.

---

### Risk analysis

**1. Locking / rewrite (PostgreSQL 16)**

Adding a nullable column without a default does not rewrite the table. It is a catalog-only change that takes a brief `ACCESS EXCLUSIVE` lock on `signups`. At ~90k rows there is no long-running table copy, and the lock is held only for the metadata update. This is the standard, cheap form of `ADD COLUMN`. No rewrite occurs.

**2. Application compatibility**

- Evidence: *"No code references `referral_source`. `grep -rn "referral_source" app/ lib/ db/` returns nothing except this migration."* — No reader or writer exists yet, so rolling deploy ordering is irrelevant.
- Evidence: *"`app/models/signup.rb` does not declare a strict attribute list that would reject extra columns."* — The new attribute will not break model instantiation or serialization.
- Because the column is nullable with no default, existing inserts and `SELECT *` paths continue to work unchanged.

**3. Data integrity**

- Existing ~90k rows get `NULL`, which is the intended state: *"Planned optional field on the signup form in a later release; existing rows stay `NULL`."*
- No constraint or backfill is needed to preserve consistency; `NULL` is a valid value for an optional field.

**4. Irreversible / destructive steps**

None. The migration is additive and trivially reversible (`DROP COLUMN`) if required.

---

### Findings

| # | Severity | Finding | Action |
|---|----------|---------|--------|
| 1 | LOW | Brief `ACCESS EXCLUSIVE` lock during `ADD COLUMN` on a ~90k-row table. Catalog-only; no rewrite. | None required. Acceptable under normal deploy windows. |

No data-integrity, rewrite, locking-escalation, or backfill concerns apply. Per `context.md`, this migration is a calibration case: it should be approved with a single LOW finding, and any claim of a rewrite, data-integrity risk, or recommended backfill would be a false positive. None are present.

---

### Verdict

**APPROVE.** The migration safely adds a nullable column with no default, no rewrite, and no application coupling. One LOW informational finding (brief exclusive lock) noted for awareness only.

---

*Note: the migration file is SQL while the path implies a Rails migration. Confirm the project's migration runner applies `.sql` files directly (e.g., via `execute` in a `.rb` wrapper). This is an execution detail outside the schema analysis and does not change the verdict.*