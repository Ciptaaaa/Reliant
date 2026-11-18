# Migration Guard Report — PG-007

**Migration:** `db/migrate/20261116103000_add_locale_to_users.sql`
**Change:** `ALTER TABLE users ADD COLUMN locale varchar(10);`
**Engine:** PostgreSQL 15 · **Table:** `users` (~310,000 rows) · **Deploy:** Rails 7 API, rolling

---

## Verdict: ✅ APPROVED

**Risk:** LOW

---

## Findings

### F1 — Nullable column add, no default (LOW)

The migration adds a single column with no default:

> `ALTER TABLE users ADD COLUMN locale varchar(10);`

On PostgreSQL 11+, adding a nullable column without a `DEFAULT` is a catalog-only operation. No heap rewrite occurs regardless of row count; prior to PG 11, a `DEFAULT` would force a rewrite, but there is none here. The `ACCESS EXCLUSIVE` lock is held only long enough to update the system catalog, which is effectively instantaneous even on a table of ~310,000 rows.

**Evidence from case context:**
> On PostgreSQL 11+, adding a nullable column with no default is metadata-only: no table rewrite, sub-millisecond, and the `ACCESS EXCLUSIVE` lock is held only for the catalog update.

**Impact:** Negligible. No long lock window, no rewrite, no backfill required.

---

## Risk assessment by category

| Category | Assessment |
|---|---|
| Lock duration | `ACCESS EXCLUSIVE`, catalog-only — sub-millisecond |
| Table rewrite | **None** (nullable, no default, PG 15) |
| Backfill required | **No** — existing rows stay `NULL` by design |
| App compatibility | **Safe** — no code references `locale` today |
| Rolling deploy | **Safe** — old and new code both tolerate the column's absence/presence |
| Data consistency | N/A — non-destructive, additive-only |

---

## Evidence reviewed

**1. No existing code depends on `locale`:**
> "Existing code references: none. The column does not exist yet and no code path reads or writes `locale`"

> "`app/models/user.rb` declares a strict attribute list (no `locale`); until the later release runs, nothing selects it."

**2. Planned usage is deferred and NULL-tolerant:**
> "a later release will read `user.locale` to choose a default interface language; absent values fall back to `en`."

> "Application writes will start using `locale` in a later release; this migration does not touch existing columns."

**3. Schema confirms target table shape:**
> `users` has `id`, `email`, `display_name`, `password_digest`, `created_at`, `updated_at` — an additive column is orthogonal to all of them.

---

## Not searched / Out of scope

- **Mobile clients** — per the application-usage note, not affected; the API does not expose `locale` until the later release.

---

## Notes (non-blocking, informational)

These are observations, not findings, and do not affect approval:

- The planned fallback to `en` for absent values is handled application-side ("absent values fall back to `en`"), consistent with keeping the column nullable and unbackfilled.
- If a future migration adds `DEFAULT` or `NOT NULL` to `locale`, that migration should be reviewed separately for rewrite/lock behavior.

---

## Recommendation

**Safe to deploy during business hours.** This is a metadata-only DDL change on PostgreSQL 15. The sole finding is `LOW` and requires no mitigation. Merge.