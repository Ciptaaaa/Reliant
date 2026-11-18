## Migration Guard Report — PG-007

**Verdict: ✅ APPROVED** — Risk level: **LOW**

### Summary

`ALTER TABLE users ADD COLUMN locale varchar(10);` adds a **nullable column with no default** to a PostgreSQL 15 table. On PostgreSQL 11+, this is a metadata-only catalog change: no table rewrite, no backfill, and the `ACCESS EXCLUSIVE` lock is held only for the brief catalog update. Safe to deploy during business hours.

### Findings

| # | Finding | Severity | Evidence |
|---|---------|----------|----------|
| 1 | Column is nullable, no `DEFAULT` clause | LOW / informational | `migration.sql`: `ADD COLUMN locale varchar(10);` — no `NOT NULL`, no `DEFAULT`. |
| 2 | Metadata-only operation (PG 11+ fast-path) | LOW / informational | `context.md`: **Engine: PostgreSQL 15**. `context.md` deployment facts: **"No default value."** |
| 3 | No existing reader/writer depends on the column | LOW / informational | `application-usage.md`: *"Existing code references: none."* and *"`app/models/user.rb` declares a strict attribute list (no `locale`); until the later release runs, nothing selects it."* |
| 4 | Lock duration | LOW / informational | `context.md`: table is **~310k rows, moderate traffic**; the `ACCESS EXCLUSIVE` lock is scoped to the catalog update only, so contention is negligible. |
| 5 | Deployment compatibility (rolling deploy) | LOW / informational | `context.md`: *"Application writes will start using `locale` in a later release; this migration does not touch existing columns."* Nullable column + no readers in current code = no runtime incompatibility across mixed-version app instances. |

**No blocking or medium findings.**

### Why this is not a rewrite

Per the case context: *"On PostgreSQL 11+, adding a nullable column with no default is metadata-only: no table rewrite, sub-millisecond, and the `ACCESS EXCLUSIVE` lock is held only for the catalog update."* The schema engine is PG 15 and the migration specifies neither `NOT NULL` nor `DEFAULT`, so the fast path applies. Existing rows remain `NULL`, consistent with the stated plan.

### Not searched / scope exclusions

- Mobile clients were **not searched** per `application-usage.md`, but *"the API does not expose `locale` until the later release"* — so no impact at this stage.
- No rollback file was supplied; for this metadata-only change, `ALTER TABLE users DROP COLUMN locale;` is the trivial reverse if ever needed.

### Notes for the later release (outside this migration's scope)

When the consumer release ships, a separate review should cover: backfill policy for existing `NULL` values (application-level fallback to `en` is already the plan per `docs/rfc/0042-localisation.md:12`), and whether any query will filter/sort on `locale` (which would warrant an index). **Neither is required for this migration.**