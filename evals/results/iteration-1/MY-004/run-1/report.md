# Migration Guard Report — MY-004

## Verdict: **BLOCK** — do not merge as written

The migration as written will **fail at apply time** (`ERROR 1452`, foreign key constraint fails) on the 47,308 orphan rows, and even if the orphans were cleaned it would break `ArchiveOldOrders` and silently change delete semantics. Three independent problems must be resolved before merge.

---

## Findings

### 1. BLOCKER — The migration will fail outright on existing orphan data
MySQL 8.0 validates foreign key data at `ADD CONSTRAINT` time; there is no `NOT VALID` escape hatch.

Evidence — `schema.sql`:
> `-- Data facts (developer ran on 2026-11-16):`
> `SELECT COUNT(*) FROM payments p LEFT JOIN orders o ON o.id = p.order_id WHERE o.id IS NULL; -> 47,308  -- orphans`
> `Orphans come from a 2021 migration that copied orders to a new table and re-keyed ids.`

Evidence — `context.md`:
> "MySQL validates FK data at `ADD CONSTRAINT` time (no `NOT VALID`), so orphans make it fail outright."

So `ALTER TABLE payments ADD CONSTRAINT ...` will abort. **Action:** backfill/relink or quarantine the 47,308 orphans (and stop the source: `ReconcileGateway.php` inserts without checking the order exists) *before* adding the constraint, ideally in a separate, verified step.

### 2. BLOCKER — `ArchiveOldOrders` will start failing under the new FK
`application-usage.md`:
> `app/Services/Orders/ArchiveOldOrders.php:41` — moves old orders to `orders_archive` and deletes from `orders` **without** deleting payments (they keep pointing at the archived ids). This path breaks under an FK.

With the proposed default action (`RESTRICT`/`NO ACTION`), deleting an order that still has payments raises `ERROR 1451 (Cannot delete or update a parent row)`. This path is currently live and will start throwing as soon as the FK lands.

**Action:** either fix `ArchiveOldOrders` (delete/repoint payments first, or use a copy-and-delete-after pattern) or explicitly choose an `ON DELETE` action — but see #3, because the developer's "cascade" assumption is wrong for *this* schema.

### 3. BLOCKER — The developer's stated rationale ("cascade") does not match the migration
`context.md`:
> "Orders are hard-deleted by the admin panel with a cascade, so this should be consistent."
> "There is no `ON DELETE` clause in the proposed FK."

The rationale assumes cascade, but the migration adds **no `ON DELETE` clause**, which in MySQL defaults to `RESTRICT`/`NO ACTION`. This is a correctness mismatch between intent and code, not just a style nit:

- `application-usage.md`:
  > "There is no `ON DELETE CASCADE` anywhere."
  > "delete order works today only because payments are deleted first."
- If someone later adds a delete path that doesn't pre-delete payments (e.g. a new admin action, or `ArchiveOldOrders` as-is), it fails — or, if `ON DELETE CASCADE` is added instead, it silently deletes payment history.

**Decision required:** pick the intended semantics explicitly and write the clause:
- `ON DELETE RESTRICT` (default) → delete services must always delete payments first; **must** fix `ArchiveOldOrders`.
- `ON DELETE CASCADE` → payment rows vanish with orders (report/audit implications; `reconcile` inserts orphans that would then be unreachable). Given payments are financial records and the app *expects* to delete them explicitly, cascade is risky without a data-retention decision.

Recommendation: **keep `RESTRICT` (explicit)**, fix `ArchiveOldOrders`, and treat payment deletion as an application responsibility — matching current behavior.

---

## Secondary notes (should be addressed, not blocking by themselves)

- **Orphan source not closed:** `ReconcileGateway.php:63` inserts payments from an external file keyed by `order_id` "does **not** check that the order exists locally." After the FK is added, these inserts will fail (good) but the reconcile job will error. It needs to validate/queue unknowns instead of hard-failing.
- **Duplicate-key/index:** there is an index `payments_order_id_idx`; no additional index is needed for the FK, and the ALTER will still need to scan `payments` (~11M rows) to validate. On MySQL 8 this takes an `ALTER` metadata lock; plan for online-DDL / low-traffic window, or use `ALGORITHM=INPLACE, LOCK=NONE` if supported (InnoDB supports online add of FK in 8.0.x with caveats). Rolling deploy won't help if the ALTER blocks writes.

---

## Required actions before this can be re-reviewed

1. Remove the 47,308 orphans (relink to existing orders or move to a quarantine table) and show the count is 0; stop `ReconcileGateway` from producing new ones.
2. Fix `ArchiveOldOrders` so it does not delete payment-bearing orders (delete/repoint payments first).
3. Decide and encode the delete semantics explicitly (`ON DELETE RESTRICT` recommended); update `DeleteOrder`/docs to match; confirm no other delete path relies on implicit cascade.
4. Re-run the orphan count and re-submit the exact final `ALTER` (including clause) plus a plan for the 11M-row validation lock.

Until 1–3 are done, this migration will either **fail to apply** (#1) or **break production order archival** (#2) — neither is acceptable for merge.