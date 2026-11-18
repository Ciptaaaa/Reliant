# Migration Guard Report — MY-004

## Verdict: **BLOCK** — do not merge as written

The migration as written will **fail on execution** (47,308 orphan rows) and, if forced through by pruning the orphans, would **silently break** `ArchiveOldOrders.php` and constrain any future order-deletion path. The developer's stated premise ("orders are hard-deleted with a cascade") is **false** — there is no cascade anywhere in the codebase.

---

## Findings

### BLOCKER 1 — `ADD CONSTRAINT` will fail: 47,308 orphan rows

Evidence (`schema.sql`):
> `SELECT COUNT(*) FROM payments p LEFT JOIN orders o ON o.id = p.order_id WHERE o.id IS NULL; -> 47,308 -- orphans`
> "Orphans come from a 2021 migration that copied orders to a new table and re-keyed ids."

MySQL 8.0 InnoDB validates existing data at `ADD CONSTRAINT` time (no `NOT VALID`). With 47,308 non-conforming `order_id` values, the `ALTER` aborts with `ERROR 1452 (Cannot add or update a child row: a foreign key constraint fails)`. The migration will not apply on production.

### BLOCKER 2 — Developer's premise is wrong; this is not a "cascade"

Evidence (`context.md`):
> "Orders are hard-deleted by the admin panel with a cascade, so this should be consistent."
> "There is no `ON DELETE` clause in the proposed FK."

Evidence (`application-usage.md`):
> "There is no `ON DELETE CASCADE` anywhere."

The proposed constraint uses the **default action** (`RESTRICT`/`NO ACTION`). Delete order works today only because `DeleteOrder.php` manually removes payments first:
> `$order->payments()->delete();   // delete payments first`
> `$order->forceDelete();          // then the order`

Any path that deletes an order without first deleting payments will now fail with `ERROR 1451 (Cannot delete or update a parent row)`. The developer believes they have a cascade; they do not.

### BLOCKER 3 — `ArchiveOldOrders.php` will start failing immediately

Evidence (`application-usage.md`):
> "`app/Services/Orders/ArchiveOldOrders.php:41` — moves old orders to `orders_archive` and deletes from `orders` **without** deleting payments (they keep pointing at the archived ids). This path breaks under an FK."

This is a known-broken call site. Under `RESTRICT`, archiving old orders throws `ERROR 1451`. Under `CASCADE` (if the developer "fixes" it that way), the payments for archived orders would be **deleted** — destroying financial records that should remain attached to the archived order ids. Neither `RESTRICT` nor `CASCADE` is correct for this path.

### CONCERN 4 — Reconciler can insert orphans by design

Evidence (`application-usage.md`):
> "`app/Console/Commands/ReconcileGateway.php:63` — inserts payments from a gateway settlement file, keyed by `order_id` taken from the file; does **not** check that the order exists locally."

Once the FK exists, any settlement row referencing a locally-unknown `order_id` (the same class of mismatch that produced the 2021 orphans) will throw `ERROR 1452` on insert. This is arguably *desirable* (surfacing bad gateway data), but it is a **behavior change** on a production insert path and must be handled (quarantine table / explicit error handling / reconciliation) *before* the FK lands, or the reconciler will start dropping settlement lines.

### NOTE 5 — Locking / operational cost on an 11M-row table

`ADD CONSTRAINT` takes a shared lock and rebuilds the index. On `payments` (~11.2M rows) this is a metadata-locking operation. Under a rolling deploy this is survivable but should go through an online-DDL runbook (e.g. gh-ost / pt-online-schema-change) or a maintenance window, not a plain `php artisan migrate` during a deploy.

---

## Required changes before re-submission

1. **Resolve the orphans explicitly** in the same migration (or a prior one), *not* by silently deleting them:
   - Re-link to the correct order ids via the 2021 copy mapping, **or**
   - Park them in a `payments_orphaned` table for reconciliation, **or**
   - After business sign-off only, delete them.
   The migration must end with the orphan count = 0 before `ADD CONSTRAINT` runs; add an assertion `SELECT ... WHERE o.id IS NULL` returning 0, and fail loudly if not.

2. **Choose the delete semantics deliberately** — do not rely on the default:
   - `ArchiveOldOrders.php` **must** be changed to copy payments to the archive (or to leave orders in place) *before* the FK is added.
   - `DeleteOrder.php` already deletes payments first — fine under `RESTRICT`. Keep `RESTRICT` unless there is a documented reason for `CASCADE`; a financial ledger should not silently cascade.
   - Write the clause explicitly (`ON DELETE RESTRICT ON UPDATE RESTRICT`) so the intent is reviewable.

3. **Handle `ReconcileGateway.php`** — add an existence check / quarantine path, or document and accept the new failure mode with alerting.

4. **Do not merge the raw `ALTER` alone.** Ship:
   - a migration that fixes/asserts the orphan count,
   - a code change for `ArchiveOldOrders.php`,
   - a code change or documented acceptance for `ReconcileGateway.php`,
   - the `ADD CONSTRAINT` (ideally run via an online-DDL tool given the 11M rows).

5. **Fix the developer's description** in the MR — the "cascade" claim is false and misleads reviewers.

---

## Summary

| Item | Status |
|---|---|
| Type compatibility (`BIGINT UNSIGNED` ↔ `BIGINT UNSIGNED`) | OK |
| Orphan rows (47,308) | **BLOCKER** — `ALTER` fails |
| Developer's "cascade" premise | **BLOCKER** — false; default is `RESTRICT` |
| `ArchiveOldOrders.php` behavior | **BLOCKER** — breaks under any FK action |
| `ReconcileGateway.php` inserts | **CONCERN** — new failure mode, needs handling |
| 11M-row online DDL | **NOTE** — run through online-DDL tool |

**Verdict: BLOCK.** The migration will fail on execution and, past that, changes delete semantics in ways the author does not appear to realize.