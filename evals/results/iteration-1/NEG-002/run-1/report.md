## Migration Guard Report — NEG-002

**Verdict: APPROVED**

**Risk: LOW**

---

### Migration reviewed

```sql
CREATE INDEX CONCURRENTLY orders_customer_placed_idx ON orders (customer_id, placed_at DESC);
```

Runner header (quoted from migration):

```
#     atomic = False          # <-- non-transactional, required for CONCURRENTLY
```

---

### Findings

**1. LOW — Long, non-blocking index build on a large table**
- Evidence: `-- Rows: ~9,000,000.` (`schema.sql`)
- `CONCURRENTLY` is present, so normal writes to `orders` (INSERT/UPDATE/DELETE) are not blocked during the build. Expect a longer runtime and extra disk/IO; schedule during a quiet window if possible. This is operational, not a correctness concern.

**2. LOW — Precaution: verify the index is valid after the build**
- Evidence: `CREATE INDEX CONCURRENTLY` (quoted above).
- `CONCURRENTLY` can leave an `INVALID` index if it fails or is cancelled. After the migration, check `pg_index.indisvalid` (or `\d orders`) and rebuild if not valid. This is a precaution; there is no evidence of a failure.

---

### Checks that passed (no finding)

- **No write blocking.** The statement is `CREATE INDEX CONCURRENTLY` (`migration.sql`), and the runner is non-transactional (`atomic = False`, `context.md`: "The migration runner supports `atomic = False` and it is set for this migration (verified in the migration file header)."). Flagging a missing transaction or write blocking here would be a false positive.
- **No destructive operation.** There is no `DROP INDEX` and no constraint change (`context.md`: "No `DROP INDEX`, no constraint change."; `application-usage.md`: "No migration in `db/migrate/` drops an index on `orders`.").
- **Index matches a real query.** Evidence:
  - `Order.objects.filter(customer_id=cid).order_by("-placed_at")[:50]` (`app/orders/views.py:41`)
  - `ON orders (customer_id, placed_at DESC)`
  - The column order `(customer_id, placed_at DESC)` lets this query use the index for both filtering and ordering, so it can avoid the sort it currently does (`application-usage.md`: "currently uses `orders_customer_id_idx` then sorts").

---

### Recommendation

Merge as-is. Optionally, after the build, confirm `indisvalid = true` for `orders_customer_placed_idx`. If it is not valid, run `REINDEX INDEX CONCURRENTLY orders_customer_placed_idx;`.