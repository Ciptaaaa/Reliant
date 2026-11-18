# Migration Guard Report — MY-004

## Verdict

**BLOCK.** This migration will fail at `ADD CONSTRAINT` time due to 47,308 orphan rows, and even if the orphans were fixed, the default `RESTRICT`/`NO ACTION` would break an existing order-archival code path. It must not be merged as written.

---

## Finding 1 — Migration fails on existing data (blocking)

MySQL validates FK data during `ALTER TABLE ... ADD CONSTRAINT` (there is no `NOT VALID` in MySQL 8.0). The developer's own query:

> `SELECT COUNT(*) FROM payments p LEFT JOIN orders o ON o.id = p.order_id WHERE o.id IS NULL; -> 47,308  -- orphans`

> "Orphans come from a 2021 migration that copied orders to a new table and re-keyed ids."

With 47,308 rows in `payments.order_id` that have no matching `orders.id`, the `ALTER TABLE` will abort with `ERROR 1452 (Cannot add or update a child row: a foreign key constraint fails)`. The migration as written cannot deploy.

The column types are compatible (`payments.order_id BIGINT UNSIGNED` → `orders.id BIGINT UNSIGNED`), so this is purely the orphan-data problem, not a type error.

**Required before merge:** resolve the 47,308 orphans (re-point them via the 2021 re-keying mapping, delete them, or move them to a quarantine table) in a prior, separate migration, and verify `COUNT(*) ... WHERE o.id IS NULL = 0` before adding the constraint.

---

## Finding 2 — Default FK action breaks an existing delete path (blocking)

The proposed FK has **no `ON DELETE` clause**, so it defaults to `RESTRICT`/`NO ACTION`. The developer's claim that "Orders are hard-deleted by the admin panel with a cascade" is **not supported by the code**:

> `app/Services/Orders/DeleteOrder.php:28` — deletes payments first, then `forceDelete()` the order. This path happens to survive `RESTRICT`.
>
> `app/Services/Orders/ArchiveOldOrders.php:41` — "moves old orders to `orders_archive` and deletes from `orders` **without** deleting payments (they keep pointing at the archived ids). This path breaks under an FK."
>
> "There is no `ON DELETE CASCADE` anywhere."

So `ArchiveOldOrders` will start failing with `ERROR 1451 (Cannot delete or update a parent row)` the moment this FK exists. The migration changes runtime behavior of a live code path and the application-usage review explicitly flags it.

**Required before merge:** either (a) fix `ArchiveOldOrders` to handle payments (delete them, or re-key/retain them against the archive) in the same release, or (b) explicitly choose an `ON DELETE` action that matches intended semantics — but note `CASCADE` would silently delete payment history, which is likely wrong for a payments table. The developer's statement about a "cascade" should be reconciled with reality: no cascade exists.

---

## Finding 3 — Write path can still introduce orphans

Not blocking the schema change by itself, but relevant to whether the constraint will hold:

> `app/Console/Commands/ReconcileGateway.php:63` — "inserts payments from a gateway settlement file, keyed by `order_id` taken from the file; does **not** check that the order exists locally."

Once the FK is in place, `ReconcileGateway` will start throwing `ERROR 1452` on settlement rows whose `order_id` is unknown locally. That is arguably correct behavior (fail closed), but it is an unhandled operational change. The team should confirm the reconcile job handles insert failures (dead-letter / retry) rather than crash-looping. The external settlement file was not inspected.

---

## Finding 4 — Locking / deploy risk on an 11M-row table (advisory)

`payments` has ~11.2M rows. On MySQL 8.0, adding an FK requires an index on `order_id` (already present: `payments_order_id_idx`) but still takes a metadata lock on `payments` and validates every row. During a rolling deploy this can hold up writes on `payments`. Plan to run it in a low-traffic window, and consider `ALGORITHM=INPLACE, LOCK=NONE` where supported — though FK addition may still require a table lock. Not a reason to block on correctness, but it should be scheduled.

---

## Summary checklist before re-review

1. ✅ Column types compatible (`BIGINT UNSIGNED` → `BIGINT UNSIGNED`) — no type error.
2. ❌ 47,308 orphans → `ADD CONSTRAINT` will fail. Must be remediated upstream.
3. ❌ Default `RESTRICT` breaks `ArchiveOldOrders::41`. Must fix code path or choose explicit action.
4. ⚠️ `ReconcileGateway` will start failing on unknown `order_id`s — confirm error handling.
5. ⚠️ Metadata lock on 11M-row table — schedule accordingly.

The migration is **not mergeable** in its current form. Re-submit after orphan cleanup and after reconciling the delete/archive semantics with the actual code paths.