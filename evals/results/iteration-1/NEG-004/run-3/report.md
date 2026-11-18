## Migration Guard Report — NEG-004

**Verdict: APPROVED**
**Risk: LOW**

### Summary

`migration.sql` adds a new `NOT NULL` column with a constant default to an existing table:

```sql
ALTER TABLE invoices ADD COLUMN currency varchar(3) NOT NULL DEFAULT 'USD';
```

On PostgreSQL 11+, adding a column with a **non-volatile** default is a metadata-only operation: the default is recorded in the catalog and existing rows synthesize it on read. There is no table rewrite and no full-table scan. This matches the documented deployment facts.

### Evidence

**1. PostgreSQL version — 16 (the rewrite-safe path).**
From `context.md`:
> "**Engine**: PostgreSQL 16"
> "- PostgreSQL 16 (confirmed in `docker-compose.yml`)."

From `schema.sql`:
> "-- Current schema (PostgreSQL 16)"

PG 11+ stores constant defaults in `pg_attribute` metadata, so `ALTER TABLE ... ADD COLUMN ... DEFAULT 'USD'` does not rewrite the table. (Note: on PG 10 and older, this *would* rewrite the table — the version dependency is the only caveat.)

**2. The default is a literal constant, not a volatile function.**
From `context.md`:
> "The default is a literal constant (`'USD'`), not a volatile function."

`DEFAULT 'USD'` is non-volatile, which is the condition that makes the fast path apply on PG 11+.

**3. No NOT NULL backfill concern — the column is new.**
From `context.md`:
> "No `NOT NULL` is being added to an existing column; the column itself is new."

With a non-null default and the column being added fresh, there are no existing NULLs to repair and no `NOT NULL` validation scan.

**4. Existing inserts remain valid and the default is observable on read.**
From `application-usage.md`:
> "existing serializer does not reference it and inserts do not specify it, so they receive `'USD'`."

> "no backfill is required because the default is stored in metadata and applied to existing rows on read."

The existing ~40,000 rows will report `currency = 'USD'` from the column default without any data movement.

**5. Nothing depends on the column that would force a rewrite.**
From `application-usage.md`:
> "No index or constraint references `currency`."

No index build or constraint validation is triggered.

**6. Table size and lock profile are benign.**
From `context.md`:
> "Table: `invoices`, ~40k rows."
> "We have already confirmed the table is ~40k rows and there are no long transactions."

The `ALTER TABLE ... ADD COLUMN` takes a brief `ACCESS EXCLUSIVE` lock to update the catalog only; at 40k rows and with no long transactions, this finishes in milliseconds. The rolling deploy is safe because new code reading `currency` sees the DB default for unwritten rows.

### Findings

- **LOW — Version dependency on the fast path.** The metadata-only behavior requires PostgreSQL 11+. This environment is confirmed on PG 16, so it applies; the caveat is recorded only for completeness.
- **No rewrite, no scan, no backfill required.** Adding a `NOT NULL DEFAULT 'USD'` column on PG 16 is the documented safe pattern; a warning about table rewrites would be a false positive here.

### Recommendation

Merge as-is. No backfill task, no `CONCURRENTLY` index step, and no phased `NOT NULL` rollout are needed.