# Migration Guard Report — NEG-004

## Verdict: ✅ APPROVED (LOW finding)

## Summary

| Field | Value |
|---|---|
| Migration | `db/migrate/20261116112000_add_currency_to_invoices.sql` |
| Operation | `ALTER TABLE invoices ADD COLUMN currency varchar(3) NOT NULL DEFAULT 'USD';` |
| Rewrite risk | **None** (PG 11+) |
| Table lock | `ACCESS EXCLUSIVE`, held only for metadata update |
| Backfill required | **No** |
| Blocking issues | **None** |

## Findings

### 1. `NOT NULL` + `DEFAULT` on a new column — SAFE (LOW)

PostgreSQL 11+ optimizes `ADD COLUMN ... DEFAULT <constant>` as a **metadata-only** operation. Because the default is a literal constant, not a volatile expression, PG stores the default in `pg_attribute` and returns it for existing rows on read — no table rewrite, no scan of the ~40,000 rows.

Evidence from `context.md`:

> "PostgreSQL 16 (confirmed in `docker-compose.yml`)."
> "The default is a literal constant (`'USD'`), not a volatile function."
> "No `NOT NULL` is being added to an existing column; the column itself is new."

The developer's reasoning is correct:

> "On PG 11+ a constant default is metadata-only, so I expect it to be fast."

**Version dependency (the only caveat):** On PostgreSQL 10 and older this *would* trigger a full table rewrite. This project targets PG 16, so it does not apply here. Mentioning it for completeness only.

### 2. No backfill task needed — CONFIRMED

Per `application-usage.md`:

> "`lib/tasks/backfill_invoices.rake` — does **not** exist; no backfill is required because the default is stored in metadata and applied to existing rows on read."

Existing rows are served `'USD'` from metadata. No data-migration step is required.

### 3. Locking — acceptable (LOW)

The `ALTER TABLE ... ADD COLUMN` takes a brief `ACCESS EXCLUSIVE` lock on `invoices`. It is metadata-only and completes quickly. With ~40k rows and the confirmed absence of long transactions (`context.md`: "there are no long transactions"), the lock footprint is negligible.

### 4. Application compatibility — OK

From `application-usage.md`:

> "`app/serializers/invoice_serializer.rb:14` — will include `currency` once merged; existing serializer does not reference it and inserts do not specify it, so they receive `'USD'`."

The column is additive, non-breaking, and inserts that omit `currency` pick up the default. Compatible with a rolling deploy.

### 5. Indexes / constraints — none affected

> "No index or constraint references `currency`." (`application-usage.md`)

No index builds, no constraint validation scans, nothing to worry about.

## Type note (informational, not a finding)

`varchar(3)` fits ISO 4217 alphabetic codes but will reject non-3-letter variants (e.g., `'US$'`) and does not enforce the code against a lookup set. If enforced validity matters later, a `CHECK` constraint can be added as a separate, non-blocking migration. Not required for this change.

## Recommendation

**Merge as-is.** No changes needed. Optionally note in the PR description that the metadata-only behavior depends on PG 11+ (satisfied here on PG 16) so it is not later re-run against an older engine.