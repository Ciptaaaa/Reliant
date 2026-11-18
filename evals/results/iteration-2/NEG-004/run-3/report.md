# Migration Guard Report — NEG-004

## Verdict

**APPROVED** — `LOW` risk.

## Migration under review

```sql
-- db/migrate/20261116112000_add_currency_to_invoices.sql
ALTER TABLE invoices ADD COLUMN currency varchar(3) NOT NULL DEFAULT 'USD';
```

## Findings

### 1. `LOW` — Constant default is metadata-only on PostgreSQL 11+ (no rewrite, no scan)

The added column has a **non-volatile literal default** (`'USD'`), on a verified **PostgreSQL 16** engine. On PG 11 and later, `ADD COLUMN … DEFAULT <constant>` stores the default in `pg_attribute.attmissingval` and does **not** rewrite the table or touch existing rows; they read the default from metadata.

Evidence:

- **Engine version:** *"PostgreSQL 16 (confirmed in `docker-compose.yml`)."*
- **Default volatility:** *"The default is a literal constant (`'USD'`), not a volatile function."*
- **No `NOT NULL` on an existing column:** *"No `NOT NULL` is being added to an existing column; the column itself is new."* — so the fast-path new-column case applies rather than a validation scan.
- **Table size is small anyway:** *"Rows: ~40,000"* and *"there are no long transactions."*
- **Read semantics:** *"the default is stored in metadata and applied to existing rows on read."*

### 2. `LOW` — No backfill required

Existing rows will surface `'USD'` via the stored default, so no data migration is needed.

Evidence:

- *"lib/tasks/backfill_invoices.rake — does **not** exist; no backfill is required because the default is stored in metadata and applied to existing rows on read."*

### 3. `LOW` (caveat) — Version dependency

This fast path depends on PG 11+. On PostgreSQL 10 and older, the same statement **would** rewrite the table. Here the engine is confirmed as PG 16, so this is not a concern for this deployment, but it is worth stating explicitly so the safety claim is not mistaken for universal.

## Application-impact check

- Serializer: *"`app/serializers/invoice_serializer.rb:14` — will include `currency` once merged; existing serializer does not reference it and inserts do not specify it, so they receive `'USD'`."* — consistent with the intended behavior.
- *"No index or constraint references `currency`."* — no index build or lock contention to consider.

## Explicitly not flagged (avoiding false positives)

- **No table rewrite.**
- **No full-table scan.**
- **No backfill task required.**
- **No `NOT NULL` validation pass** (the column is new, not pre-existing).

## Recommendation

Merge as-is. Optional follow-ups, none blocking: keep the default constant (never replace it with a volatile expression such as `now()` or a UUID function without revisiting this analysis), and note the PG 11+ dependency in the PR description for future readers.