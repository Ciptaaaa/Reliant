# Migration Guard Report — NEG-002

## Verdict: **APPROVED** ✅

## Summary

| Field | Value |
|---|---|
| Migration | `db/migrate/20261116111000_add_orders_customer_placed_idx.py` |
| Operation | `CREATE INDEX CONCURRENTLY orders_customer_placed_idx ON orders (customer_id, placed_at DESC)` |
| Table | `orders` (~9,000,000 rows) |
| Destructive | No |
| Blocking risk | None (non-blocking build) |
| Finding severity | **LOW** |
| Action required | None before merge |

## Evidence

**1. Index is `CONCURRENTLY` — does not block writes.**
```sql
CREATE INDEX CONCURRENTLY orders_customer_placed_idx ON orders (customer_id, placed_at DESC);
```
`CONCURRENTLY` builds the index without taking a lock that blocks `INSERT`/`UPDATE`/`DELETE` on `orders`. Safe under the rolling deploy.

**2. Runner is configured non-transactional (required for `CONCURRENTLY`).**
From `migration.sql` header:
```
#     atomic = False          # <-- non-transactional, required for CONCURRENTLY
```
From `context.md`: *"The migration runner supports `atomic = False` and it is set for this migration (verified in the migration file header)."* This satisfies PostgreSQL's requirement that `CREATE INDEX CONCURRENTLY` run outside a transaction block.

**3. No destructive operations.**
From `application-usage.md`: *"No migration in `db/migrate/` drops an index on `orders`. No code path depends on the index existing."* `context.md`: *"No `DROP INDEX`, no constraint change."*

**4. Index matches the application query.**
From `app/orders/views.py:41`:
```python
Order.objects.filter(customer_id=cid).order_by("-placed_at")[:50]
```
The composite `(customer_id, placed_at DESC)` supports the equality filter on `customer_id` followed by the descending sort on `placed_at`, avoiding the current `orders_customer_id_idx` + sort plan described in `application-usage.md`.

## Findings

**LOW — Long but non-blocking build.**
Building an index `CONCURRENTLY` on ~9M rows will take time and consume I/O, but it does not block application writes. It runs two table scans (index build + validation). No user impact beyond background load.

**LOW (precaution) — Verify the index is valid after the build.**
If a `CONCURRENTLY` build is interrupted, PostgreSQL leaves an *invalid* index behind that consumes space and is ignored by the planner. Recommended post-migration check:
```sql
SELECT indexrelid::regclass AS index, indisvalid
FROM pg_index
WHERE indexrelid = 'orders_customer_placed_idx'::regclass;
```
Expect `indisvalid = t`. If `f`, drop and re-create:
```sql
DROP INDEX CONCURRENTLY orders_customer_placed_idx;
```

## Explicitly not flagged (avoiding false positives)

- **Write blocking** — not applicable; `CONCURRENTLY` is precisely the lock-avoiding form.
- **Missing transaction** — not a defect; `atomic = False` is required and confirmed.
- **Unsafe `DROP`** — none present.
- **Redundant index warning** — `orders_customer_id_idx` remains, which is fine; the new index is not a strict superset requirement to justify removing it in the same migration, and removing it would be an unnecessary risk.

## Recommendation

**Approved for merge.** Proceed as written. After the deploy, confirm `indisvalid = t` for `orders_customer_placed_idx` as a precaution, and optionally capture a before/after `EXPLAIN` for `views.py:41` to confirm the sort is eliminated.