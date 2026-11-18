# Migration Guard Report — NEG-002

## Summary

`CREATE INDEX CONCURRENTLY orders_customer_placed_idx ON orders (customer_id, placed_at DESC);`

Target table: `orders`, ~9,000,000 rows (PostgreSQL 16, Django 5, rolling deploy).

## Findings

### [LOW] Index build is long but non-blocking; invalid-index state remains if build fails

**Evidence**
- Migration uses `CONCURRENTLY`:
  > `CREATE INDEX CONCURRENTLY orders_customer_placed_idx ON orders (customer_id, placed_at DESC);`
- Runner is non-transactional (required for `CONCURRENTLY`):
  > `atomic = False          # <-- non-transactional, required for CONCURRENTLY`
- Table size:
  > `-- Rows: ~9,000,000.`

`CONCURRENTLY` avoids blocking writes on `orders` during the build. On a 9M-row table the build may still take
a while, but it does not take an `ACCESS EXCLUSIVE` lock for the duration and does not block DML.

If the concurrent build is interrupted or fails, PostgreSQL leaves an **invalid** index behind. It is not used by
the planner and would need `DROP INDEX CONCURRENTLY` + rebuild. Worth checking after deploy:
`SELECT indisvalid FROM pg_index WHERE indexrelid = 'orders_customer_placed_idx'::regclass;`

**Verdict impact:** none. This is a precaution, not a defect.

## Checks performed (and why no blocking issues)

- **Write blocking:** No. `CONCURRENTLY` is present, spelled correctly, and the runner is `atomic = False` as required.
- **Transaction requirement:** Satisfied — `atomic = False` is verified in the migration header.
- **Destructive operations:** None. No `DROP INDEX`, no constraint changes.
- **Code dependency / index usage:** The target query matches the index shape:
  > `Order.objects.filter(customer_id=cid).order_by("-placed_at")[:50]`
  > `→ the query this index targets; currently uses orders_customer_id_idx then sorts.`

  `(customer_id, placed_at DESC)` serves the equality filter and the descending sort, and `LIMIT 50` can stop
  early on the index.
- **Redundancy:** `orders_customer_id_idx ON orders (customer_id)` becomes largely subsumed by the new composite
  index for this query. Removing it is optional and not part of this migration; no action required.

## Verdict: **APPROVED**

Low-risk change. The `CONCURRENTLY` build is correctly ordered and configured for the non-transactional runner,
no writes are blocked, and no destructive operations are present. Only follow-up is the post-deploy
invalid-index check noted above.