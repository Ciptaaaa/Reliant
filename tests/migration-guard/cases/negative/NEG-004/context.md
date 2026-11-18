# NEG-004 — Add a constant default via the safe path

## Context

- **Engine**: PostgreSQL 16
- **Application**: Rails 7 API, rolling deploy
- **What the developer said**:
  > We need a `currency` column on `invoices` that defaults to `'USD'`, not nullable, for new rows. We
  > have already confirmed the table is ~40k rows and there are no long transactions. Migration:
  > `ALTER TABLE invoices ADD COLUMN currency varchar(3) NOT NULL DEFAULT 'USD';`
  > On PG 11+ a constant default is metadata-only, so I expect it to be fast.
- **Table**: `invoices`, ~40k rows.

## Deployment facts

- PostgreSQL 16 (confirmed in `docker-compose.yml`).
- The default is a literal constant (`'USD'`), not a volatile function.
- No `NOT NULL` is being added to an existing column; the column itself is new.

## Why this case exists (negative)

This is a safe migration that *looks* risky to a pattern-matching agent: `NOT NULL` plus a default on an
existing table. On PostgreSQL 11+ with a non-volatile default, no rewrite occurs and existing rows read
the default from metadata. Correct answer: **APPROVED** with a `LOW` finding. A report that flags a
rewrite, a table scan, or a backfill requirement is a false positive. (The one acceptable caveat is
that on PG 10 and older this *does* rewrite — so a report that mentions the version dependency while
approving is the strongest answer.)
