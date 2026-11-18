# Migration Guard Report — MY-004

**Verdict: DO NOT MERGE.** The migration will fail immediately (orphan rows), and even if the orphans were fixed it would introduce a `RESTRICT` FK that breaks the `ArchiveOldOrders` code path.

---

## Findings

### 1. BLOCKER — `ADD CONSTRAINT` will fail on existing orphan rows

The migration:

```sql
ALTER TABLE `payments`
  ADD CONSTRAINT `payments_order_id_fk` FOREIGN KEY (`order_id`) REFERENCES `orders` (`id`);
```

MySQL 8.0 validates FK data at `ADD CONSTRAINT` time (there is no `NOT VALID` escape hatch on InnoDB). The data facts confirm orphans exist:

> `SELECT COUNT(*) FROM payments p LEFT JOIN orders o ON o.id = p.order_id WHERE o.id IS NULL;` → **47,308** — orphans

> Orphans come from a 2021 migration that copied orders to a new table and re-keyed ids.

So this statement errors out (errno 1452, "Cannot add or update a child row: a foreign key constraint fails") rather than being a harmless no-op. **The migration does not apply.**

Root cause is known and is not UI-generated: a 2021 re-keying left payments pointing at ids that no longer match `orders.id`. Re-pointing them requires the old→new id mapping, which isn't in this repo.

**Fix options (pick one, in a *separate* migration before this one):**
- Backfill/remap orphans to correct `orders.id` using the 2021 id mapping, then re-run.
- Or, if the 47,308 payments are genuinely unrecoverable/not needed, delete them (with a hold on the gateway reconciliation source so `ReconcileGateway` doesn't recreate them).
- Don't proceed to add the FK until the orphan count is zero.

### 2. BLOCKER — default `ON DELETE` (RESTRICT/NO ACTION) breaks `ArchiveOldOrders`

There is **no `ON DELETE` clause**, so the FK defaults to `RESTRICT`/`NO ACTION`. Per `application-usage.md`:

> `app/Services/Orders/ArchiveOldOrders.php:41` — moves old orders to `orders_archive` and deletes from `orders` **without** deleting payments (they keep pointing at the archived ids). This path breaks under an FK.

So the moment the FK exists, archiving an order with any payment raises `ERROR 1451 (Cannot delete or update a parent row)`. This is a data-path regression, not a one-off: it will fire on every archived order that has a payment.

The developer's rationale — "Orders are hard-deleted by the admin panel with a cascade" — is **wrong for this codebase**:

> `DeleteOrder.php:28` … `$order->payments()->delete(); // delete payments first` → delete order works today only because payments are deleted first. … There is no `ON DELETE CASCADE` anywhere.

The current deletes only succeed because payments are deleted first in application code. That coincidence does not survive the archive path, which explicitly does not delete payments.

**Decision required before merge:** choose the delete semantics and make the code match.
- If archived orders legitimately keep their payments → the FK must be `ON DELETE RESTRICT` **and** `ArchiveOldOrders` must stop hard-deleting orders that have payments (e.g., soft-delete or re-parent first). `RESTRICT` is a correct *guard* here — it is surfacing a real inconsistency.
- If payments are meant to follow orders on archive/delete → `ON DELETE CASCADE` (or re-point `payments.order_id` to `orders_archive` in the archive service). CASCADE is **not** safe while `ReconcileGateway` can insert payments for non-existent orders (see below), since a cascade would silently delete payment rows that the gateway still considers settled — that's accounting data loss.

Either way, this cannot ship as-is.

### 3. WARNING — `ReconcileGateway` can (re)create orphans after the FK is added

> `app/Console/Commands/ReconcileGateway.php:63` — inserts payments … keyed by `order_id` taken from the file; does **not** check that the order exists locally.

With the FK in place, the first orphan-producing reconcile run will fail with 1452 (insert rejected) or, worse, whichever error path the command has will drop settlement rows on the floor. The command needs to validate/resolve `order_id` (or the file provides orders ahead of payments) before the FK lands. Note also: `ReconcileGateway` pulling `order_id` from the gateway file is plausibly the origin of past orphans; the "not searched — external file" gap should be closed for the 47k orphans specifically.

### 4. Sizing / operational notes (not blocking on their own, but relevant)

- `payments` ~11.2M rows, `orders` ~3.2M. `ADD CONSTRAINT` with validation on InnoDB takes a metadata lock and can run a full validation scan; expect a write stall on `payments` for the duration. On 8.0.36 this is `CREATE TABLE ... ` copy? No — InnoDB validates inline under an `ALTER ... ALGORITHM=INPLACE` for FK add, which still blocks writes to the child table while it validates. Plan a maintenance window or use `pt-online-schema-change` / `gh-ost` (both can add FKs via the shadow table).
- Column types are compatible (`BIGINT UNSIGNED` ← `BIGINT UNSIGNED`), so this is **not** the type-mismatch trap.

---

## Summary table

| # | Severity | Finding | Evidence |
|---|----------|---------|----------|
| 1 | BLOCKER | Migration fails: 47,308 orphan `payments.order_id` rows | `schema.sql` data facts; MySQL validates at `ADD CONSTRAINT` |
| 2 | BLOCKER | Default `RESTRICT` breaks `ArchiveOldOrders` (`ERROR 1451`) | `application-usage.md` lines 41, 28–31 |
| 3 | WARNING | `ReconcileGateway` inserts unvalidated `order_id` → new orphans post-FK | `application-usage.md:63` |
| 4 | NOTE | Inline validation on 11M-row child table → write stall during migration | `schema.sql` row counts |
| 5 | NOTE | Developer's "cascade" claim contradicted by code — no `ON DELETE CASCADE` exists | `application-usage.md`, `context.md` |

**Verdict: DO NOT MERGE.**

Required order of operations:
1. Resolve the 47,308 orphans (remap or delete) in a prior migration; re-verify count = 0.
2. Decide intended delete semantics; either change the FK to an explicit `ON DELETE` clause **and** update `ArchiveOldOrders`, or keep `RESTRICT` and change `ArchiveOldOrders` to not hard-delete orders with payments.
3. Harden `ReconcileGateway` to resolve `order_id` before insert.
4. Re-submit this migration with the explicit `ON DELETE` clause, plus a maintenance-window plan for the 11M-row validation.

The developer's `ON DELETE CASCADE` rationale in `context.md` should also be corrected in the ticket: the codebase does not cascade today, and adding a cascade without addressing findings 2–3 risks deleting settlement records the gateway still references.