# Migration Guard Report — NEG-002

## Verdict

**APPROVED** — LOW severity

## Summary

The migration adds a composite index on `orders (customer_id, placed_at DESC)` using `CREATE INDEX CONCURRENTLY`, on a table of ~9M rows. It matches an actual query pattern in the application, uses no transaction (required for `CONCURRENTLY`), and performs no destructive operations. No blocking writes, missing-transaction hazard, or unsafe drop is present.

## Findings

### LOW — Long but non-blocking index build; verify the index becomes valid

- **Evidence** (`migration.sql`):
  > `CREATE INDEX CONCURRENTLY orders_customer_placed_idx ON orders (customer_id, placed_at DESC);`
- **Evidence** (`schema.sql`):
  > `-- Rows: ~9,000,000.`
- **Assessment**: `CONCURRENTLY` allows reads and writes to continue during the build, so the ~9M-row table size means a long build, not a lock. If a build is interrupted, PostgreSQL leaves an `INVALID` index behind that still consumes write overhead and will not be used for queries. As a precaution, after deploy confirm it is valid:

  ```sql
  SELECT c.relname, i.indisvalid, i.indisready
  FROM pg_index i JOIN pg_class c ON c.oid = i.indexrelid
  WHERE c.relname = 'orders_customer_placed_idx';
  ```
  Expect `indisvalid = t` and `indisready = t`. If invalid, run `DROP INDEX CONCURRENTLY orders_customer_placed_idx;` and re-run the migration.

### INFO — Migration runner configuration is correct

- **Evidence** (`migration.sql` header):
  > `atomic = False          # <-- non-transactional, required for CONCURRENTLY`
- **Evidence** (`context.md`):
  > "The migration runner supports `atomic = False` and it is set for this migration (verified in the migration file header)."
- **Assessment**: `CONCURRENTLY` cannot run inside a transaction block. The runner is explicitly set to non-transactional, so this requirement is met. No leading or trailing issue.

### INFO — Index targets a real query; redundant single-column index is retained by design

- **Evidence** (`application-usage.md`):
  > `Order.objects.filter(customer_id=cid).order_by("-placed_at")[:50]`
  > "→ the query this index targets; currently uses `orders_customer_id_idx` then sorts."
- **Assessment**: The composite index `(customer_id, placed_at DESC)` serves the filter-and-sort in one pass and can replace the filter-plus-sort plan. `orders_customer_id_idx` becomes largely redundant (the composite covers its prefix), but it is deliberately not dropped here.
- **Evidence** (`application-usage.md`):
  > "No migration in `db/migrate/` drops an index on `orders`."
  > "No code path depends on the index existing."

  Leaving it in place is a safe, non-destructive choice; a later cleanup migration can drop it with `DROP INDEX CONCURRENTLY` once this index is confirmed valid and in use. This is not required for approval.

### INFO — No lock or write-blocking hazard

- **Evidence** (`context.md`):
  > "No `DROP INDEX`, no constraint change."
  > "The migration is marked non-transactional in the runner."
- **Assessment**: A `CREATE INDEX CONCURRENTLY` takes only a share-update-exclusive lock briefly at start and end, which does not block reads or writes. There is no `ACCESS EXCLUSIVE` lock, no table rewrite, and no destructive DDL. Row-level write latency may increase slightly during the build due to index maintenance, which is normal and non-blocking.

## Checklist

| Check | Result |
|---|---|
| Transactional DDL around `CONCURRENTLY` | Pass — `atomic = False` |
| Blocking locks (write/read) | Pass — none |
| Destructive operations (DROP/TRUNCATE/rewrite) | Pass — none |
| Constraint changes / validation scans | Pass — none |
| Rollback path | Non-destructive; rollback is a plain `DROP INDEX CONCURRENTLY` if ever needed |
| Post-deploy validity check documented | Yes — see LOW finding |

## Required actions

None blocking. Recommended:

1. After the migration completes, run the `pg_index` check above to confirm `indisvalid`.
2. Optionally, in a separate later migration, drop `orders_customer_id_idx` with `DROP INDEX CONCURRENTLY` once the composite index is confirmed and in use.

**Verdict: APPROVED (LOW).**