## Migration Guard Report — NEG-002

**Verdict: APPROVED**

### Summary

`migration.sql` adds a composite index on `orders (customer_id, placed_at DESC)` using `CREATE INDEX CONCURRENTLY`. The index matches the exact predicate and sort order of the hot query in `app/orders/views.py:41`:

```python
Order.objects.filter(customer_id=cid).order_by("-placed_at")[:50]
```

The migration is non-transactional (`atomic = False` recorded in the file header, and the runner behavior is confirmed in `context.md`), which is required for `CONCURRENTLY`. No destructive operations are present.

### Findings

| Severity | Finding | Evidence |
|---|---|---|
| **LOW** | Long, non-blocking index build on a ~9M-row table; brief resource consumption (extra CPU/I/O, WAL). | `-- Rows: ~9,000,000.` in `schema.sql`; `CREATE INDEX CONCURRENTLY`. |
| **LOW** (precaution) | If a `CONCURRENTLY` build fails, PostgreSQL leaves an `INVALID` index behind that is silently ignored by the planner. Check `pg_index.indisvalid` after deploy and drop/recreate if invalid. | Standard `CONCURRENTLY` behavior; migration contains a single `RunSQL` with no cleanup step. |

### Checks performed

- **Write blocking**: None. `CONCURRENTLY` avoids `ACCESS EXCLUSIVE`/write locks on `orders`.
  - Evidence: `CREATE INDEX CONCURRENTLY orders_customer_placed_idx ...`
- **Transaction requirement**: Satisfied. `CONCURRENTLY` cannot run inside a transaction block; the migration sets `atomic = False` (quoted in `migration.sql` header and confirmed by `context.md`: "The migration runner supports `atomic = False` and it is set for this migration (verified in the migration file header).").
- **Destructive operations**: None. No `DROP INDEX`, no `DROP COLUMN`, no constraint changes.
  - Evidence: `application-usage.md`: "No migration in `db/migrate/` drops an index on `orders`." and `context.md`: "No `DROP INDEX`, no constraint change."
- **Usability of the index**: The index column order `(customer_id, placed_at DESC)` matches the equality filter followed by descending sort, so it supports the query without a sort step.
  - Evidence: `application-usage.md`: "the query this index targets; currently uses `orders_customer_id_idx` then sorts."
- **Rollback**: A plain `DROP INDEX CONCURRENTLY orders_customer_placed_idx;` would be the reverse. Not required at merge time, and no code path depends on the index.
  - Evidence: `application-usage.md`: "No code path depends on the index existing."

### Notes (not blockers)

- The old index `orders_customer_id_idx` becomes largely redundant for this access pattern. Removing it could be considered later as a separate `DROP INDEX CONCURRENTLY` migration. This is not required now, and it must not be bundled into this change.

**Result: APPROVED — LOW findings only (non-blocking build; verify index validity after deploy).**