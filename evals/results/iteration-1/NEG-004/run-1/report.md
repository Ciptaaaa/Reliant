## Migration Guard Report — NEG-004

**Migration:** `db/migrate/20261116112000_add_currency_to_invoices.sql`
**Statement:** `ALTER TABLE invoices ADD COLUMN currency varchar(3) NOT NULL DEFAULT 'USD';`

### Verdict: ✅ APPROVED

### Risk level: LOW

### Findings

**F-1 (LOW) — Constant default on a new column is metadata-only on PG 11+; no rewrite, no backfill.**
This is the expected-safe path. Evidence:
- `schema.sql` confirms **PostgreSQL 16**, well past the PG 11 threshold where `ADD COLUMN ... DEFAULT <constant>` became metadata-only.
- `context.md` confirms the default is a literal constant: *"The default is a literal constant (`'USD'`), not a volatile function."*
- `context.md` confirms no rewrite condition is present: *"No `NOT NULL` is being added to an existing column; the column itself is new."*
- `application-usage.md` confirms no backfill is required: *"`lib/tasks/backfill_invoices.rake` — does **not** exist; no backfill is required because the default is stored in metadata and applied to existing rows on read."*
- `application-usage.md` confirms no index/constraint interaction: *"No index or constraint references `currency`."*

Only the catalog is touched; the ~40k rows are not rewritten and existing rows read `'USD'` from the default metadata.

**F-2 (informational, not a blocker) — Version dependency worth noting.**
The metadata-only behavior relies on PostgreSQL 11+. Per `context.md` and the case notes, on PG 10 and older this same statement *would* rewrite the table. This environment is confirmed PG 16, so it is not a concern here, but the migration's safety is version-dependent.

**F-3 (informational) — Rolling deploy / serializer note.**
`application-usage.md` states `app/serializers/invoice_serializer.rb:14` *"will include `currency` once merged"* and that existing inserts do not specify it, so they receive `'USD'`. During a rolling deploy, the schema must be in place before the serializer that reads `currency` is live — standard add-column-before-deploy ordering. Because the column is `NOT NULL DEFAULT 'USD'`, old application instances that insert without specifying currency remain valid. No action required, just ordering awareness.

### Explicitly *not* flagged (avoiding false positives)
- **No table rewrite / no long `ACCESS EXCLUSIVE` scan** — constant default on PG 11+ is metadata-only.
- **No backfill required** — existing rows get the value from metadata on read; there is no backfill task.
- **No blocking lock issue** — the operation is a brief catalog-only change; `context.md` confirms *"no long transactions."*

### Recommendation
Merge. Optionally add a one-line comment in the migration noting the PG 11+ constant-default guarantee, so the reasoning survives for future readers (and so anyone targeting older PostgreSQL sees the caveat).