# Migration Guard Report — `db/migrate/20261116104000_add_referral_source_to_signups.sql`

**Verdict: APPROVED** — Risk: **LOW**

## Summary

The migration adds one nullable `varchar(64)` column to `signups` with no default, no constraint, no index, and no backfill. This is the low-risk path on PostgreSQL 16, and the evidence supports it.

## Evidence reviewed

1. **Column definition** (`migration.sql`):
   ```sql
   ALTER TABLE signups ADD COLUMN referral_source varchar(64);
   ```
   No `DEFAULT` clause. On PostgreSQL 11+, adding a nullable column with no default does not rewrite the table. It only updates the catalog, so the ~90k rows are not touched.

2. **Table size and shape** (`schema.sql`):
   - `signups` has about 90,000 rows, which is small.
   - The table already has a nullable free-text column (`source_page varchar(64)`), so this column follows an existing pattern and needs no new invariant.

3. **Application usage** (`application-usage.md`):
   - "No code references `referral_source`." Nothing in `app/`, `lib/`, or `db/` reads or writes the column yet, so no query behavior can change.
   - "Planned optional field ... existing rows stay `NULL`." A null is the intended state, so no backfill is needed.
   - "`app/models/signup.rb` does not declare a strict attribute list that would reject extra columns." The rollout will not break deserialization.

4. **Deployment facts** (`context.md`):
   - "No default, no constraint, no index, no backfill in this migration."
   - "No existing query modified."
   - Rolling deploy: the column is nullable and nobody references it, so a mixed-version fleet is consistent. The new column is simply unread.

## Risk assessment

| Dimension | Finding |
|---|---|
| Locking | A brief `ACCESS EXCLUSIVE` lock, taken only to update the catalog (sub-millisecond at this size) |
| Table rewrite | None: nullable column, no default |
| Data integrity | None: existing rows are `NULL` by design, and there is no constraint to violate |
| Backfill needed | No: the field is optional and the app tolerates `NULL` |
| Rollout / rollback | Safe: nothing reads the column, and dropping a nullable column is trivial if reverted |

## Required actions

None.

## Explicitly not flagged (avoiding false positives)

- **No data-integrity risk**: The column is optional and nullable by intent.
- **No rewrite**: PostgreSQL 16 adds a nullable column without a default in place.
- **No backfill recommendation**: The null value is the correct state for existing rows, and the field is not populated yet.