# Migration Guard Report — NEG-002

**Migration:** `db/migrate/20261116111000_add_orders_customer_placed_idx.py`
**Statement:**
```sql
CREATE INDEX CONCURRENTLY orders_customer_placed_idx ON orders (customer_id, placed_at DESC);
```
**Engine:** PostgreSQL 16 · **Table:** `orders` (~9,000,000 rows) · **Verdict: ✅ APPROVED (LOW risk)**

---

## Findings

### 1. [LOW] Long-running non-blocking index build on a 9M-row table
The index is built with `CONCURRENTLY`:
> `CREATE INDEX CONCURRENTLY orders_customer_placed_idx ON orders (customer_id, placed_at DESC);`

`CONCURRENTLY` avoids the `ACCESS EXCLUSIVE` lock that a plain `CREATE INDEX` would take, so writes to `orders` continue during the build. However, it is not free: the build does two table scans plus a `pg_index` state change, takes a `SHARE UPDATE EXCLUSIVE` lock (blocks other DDL/`VACUUM` on the table, not DML), and on a ~9M-row table may take minutes. **Non-blocking, but long.** Expect elevated I/O and a longer migration step; this is the expected cost, not a defect.

### 2. [LOW — precaution] Failed `CONCURRENTLY` build leaves an `INVALID` index
`CREATE INDEX CONCURRENTLY` runs as multiple transactions. If it is interrupted (timeout, connection loss, deploy abort), PostgreSQL leaves behind an index flagged invalid — present in the catalog, ignored by the planner, and still incurring write overhead. The migration is correctly marked non-transactional:
> `atomic = False          # <-- non-transactional, required for CONCURRENTLY`

**Recommended post-deploy check:**
```sql
SELECT c.relname, i.indisvalid, i.indisready
FROM pg_index i JOIN pg_class c ON c.oid = i.indexrelid
WHERE c.relname = 'orders_customer_placed_idx';
```
If `indisvalid = false`, run `DROP INDEX CONCURRENTLY orders_customer_placed_idx;` and re-run the migration. Also confirm no redundant duplicate exists (`pg_index` / `\d orders`).

---

## Checks performed — no issues found

| Check | Result | Evidence |
|---|---|---|
| Destructive operations (`DROP INDEX`, `DROP COLUMN`, etc.) | **None** | `application-usage.md`: "No migration in `db/migrate/` drops an index on `orders`." Statement is a single `CREATE INDEX CONCURRENTLY`. |
| Write blocking on `orders` | **None** | `CONCURRENTLY` is used; no `ACCESS EXCLUSIVE` lock is taken. |
| Transaction requirement | **Satisfied** | `migration.sql`: `atomic = False  # <-- non-transactional, required for CONCURRENTLY`; `context.md`: "The migration runner supports `atomic = False` and it is set for this migration (verified in the migration file header)." `CONCURRENTLY` cannot run inside a transaction block, so a transactional migration would fail — this is correctly configured. |
| Constraint changes | **None** | `context.md`: "No `DROP INDEX`, no constraint change." |
| Index matches the hot query | **Yes** | `views.py:41`: `Order.objects.filter(customer_id=cid).order_by("-placed_at")[:50]` — column order `(customer_id, placed_at DESC)` matches equality-on-`customer_id` + descending sort/limit-50 exactly, so the planner can use it for the filter and satisfy the `ORDER BY` without a sort. |
| Redundant/duplicate index | **No** | Existing index is `orders_customer_id_idx ON orders (customer_id)` (a leading-prefix subset); the composite index is not a duplicate. |
| Downstream code depends on index existence | **No** | `application-usage.md`: "No code path depends on the index existing." |

---

## Verdict

**APPROVED — LOW risk.**

This is the intended pattern for adding an index on a large, live table: `CONCURRENTLY` keeps writes flowing, `atomic = False` is set and supported by the runner, the index column order exactly matches the query at `app/orders/views.py:41`, and there are no destructive or constraint operations. The only residual items are the inherent (non-blocking) build time on ~9M rows and the standard precaution of verifying `indisvalid` afterward in case of an interrupted build.

**Suggested follow-up (non-blocking):** run the `indisvalid` check after deploy; if the build was interrupted, drop the invalid index concurrently and re-apply. No changes to the migration are required before merge.