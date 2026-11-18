# Migration Guard Report — MY-004

**Migration:** `database/migrations/2026_11_16_123000_payments_order_fk.php` (raw SQL form) — add FK `payments_order_id_fk` on `payments.order_id → orders.id`

**Verdict: ❌ DO NOT MERGE / DO NOT APPLY AS-IS.** This migration will (a) fail outright against production data on two counts, and (b) even if it were made to succeed, silently change delete semantics in ways that break at least one existing code path. Four blocking findings.

---

## Blocking findings

### 1. Orphan rows — `ADD CONSTRAINT` will fail on 47,308 rows

The migration has no `NOT VALID` escape hatch; MySQL 8.0 InnoDB validates existing data at `ADD CONSTRAINT` time and aborts on any violation.

Evidence:

> ```
> SELECT COUNT(*) FROM payments p
>   LEFT JOIN orders o ON o.id = p.order_id WHERE o.id IS NULL;  -> 47,308  -- orphans
> ```
> — `schema.sql` (data facts, 2026-11-16)

The FK will be rejected. On an 11M-row table this is also a full table scan before failure, so it's not free even in the failure mode. The `NOT VALID` escape and `ALGORITHM=INSTANT` path do not exist for FK addition fail-fast.

**Requires:** a data-remediation migration that runs *first* and *before* this one, e.g.:

- Identify the orphans and decide per-row: backfill `orders` stubs, repoint to the correct surviving order, or quarantine (a `payments_orphaned` table or an `orphaned` flag) so reconciliation history is preserved for accounting. The row count and the 2021 re-keying origin mean these are not "garbage"; they're a ledger of a past migration and must be triaged by someone who owns the ledger, not deleted blind.
- Verification query that must return `0` before the FK migration is merged downstream: the same `LEFT JOIN ... IS NULL` count.

### 2. No `ON DELETE` clause = `RESTRICT`/`NO ACTION`, which breaks `ArchiveOldOrders`

The FK as written uses the default. The developer's context claims the cascade exists:

> "Orders are hard-deleted by the admin panel **with a cascade**, so this should be consistent."
> — `context.md`
> "There is no `ON DELETE CASCADE` anywhere."
> — `application-usage.md`, Delete order section

The developer's mental model is wrong, and the actual code paths confirm it.

- `DeleteOrder.php` deletes payments *first*, so it survives today under `RESTRICT` only by doing it manually. Its transaction does not need `CASCADE`, but it **depends on the delete-payments-first ordering**, and nothing enforces that ordering for future callers.
- `ArchiveOldOrders.php:41` — **breaks immediately**:
  > "moves old orders to `orders_archive` and deletes from `orders` **without** deleting payments (they keep pointing at the archived ids). This path breaks under an FK."
  > — `application-usage.md`, `ArchiveOldOrders.php` section

Under today's schema (no FK) this works; under the migration, every archived order with payments throws `ERROR 1451 (Cannot delete or update a parent row)`. This alone means the migration is a backwards-compatibility break for a scheduled job.

**Requires (choose one, and state the choice in the migration itself, not only in the PR description):**

- **Preferred:** add explicit `ON DELETE RESTRICT` (not cascade!) and fix `ArchiveOldOrders` to either (i) not physically delete `orders` — mark archived and leave the FK target in place, or (ii) delete payments first under the same transaction, matching `DeleteOrder` semantics. The FK then does what the code already does; it just enforces it.
- **Do NOT** add `ON DELETE CASCADE` to "make the developer's words true." That would make `DeleteOrder` continue to work, but would also silently gut a financial ledger if anything ever deletes an order by mistake. The application treats payments as financial records; cascade deletion on `orders` is a data-loss footgun. `RESTRICT` is the correct default for money.

### 3. `ReconcileGateway` inserts can violate the new constraint at runtime

> "`app/Console/Commands/ReconcileGateway.php:63` — inserts payments from a gateway settlement file, keyed by `order_id` taken from the file; does **not** check that the order exists locally."
> — `application-usage.md`, Writes

The FK will not just be a migration-time problem; it becomes a **steady-state runtime** failure. Current orphans are the 2021 migration's; new ones will be generated every settlement run. If the reconciliation inserts run inside a transaction with retries, the FK failure will roll back the whole batch, and instead of a *reconciliation* you get a *reconciliation outage*. This is not hypothetical — the existing 47k orphans are evidence that gateway `order_id` values do not always match local orders (returns, test mode, merchant id reuse, etc.).

**Requires:** before shipping the FK, `ReconcileGateway` must either (a) validate and skip/quarantine unknown `order_id`s with an explicit report, or (b) be moved behind the quarantine table proposed in finding 1. This is an application change that must ship *with* or *before* the migration, not after.

### 4. Deployment / lock and rolling-deploy risk

- `ALTER TABLE payments ADD CONSTRAINT ...` on an 11M InnoDB table takes a metadata lock and, for FK validation, a full scan. MySQL 8.0's `ALGORITHM=INPLACE` support for adding FKs is `INPLACE`/`LOCK=SHARED` in good versions, but with the orphans it will still fail; and with a rolling deploy of Laravel, old app workers may still be inserting/updating during the migration. Any FK validation must run when no writer is producing invalid rows.
- The rolling deploy note matters: the FK cannot precede the application fix for `ArchiveOldOrders`/`ReconcileGateway` if those run as scheduled jobs, or you get a known-broken state between migration completion and the next app deploy.

---

## Non-blocking, but confirm

- `payments.order_id` is `BIGINT UNSIGNED NOT NULL`; `orders.id` is `BIGINT UNSIGNED NOT NULL AUTO_INCREMENT`. Types match — good, no type-mismatch error. The `LEFT JOIN` orphan count also confirms `NULL` is not in play here, consistent with `NOT NULL`.
- Index coverage: adding the FK will typically **need an index on the child column**; `payments_order_id_idx (order_id)` already exists — good. Confirm the planner will use it for FK checks (it will).
- Concurrent DDL: on 8.0.36 use `ALGORITHM=INPLACE, LOCK=SHARED` explicitly if you want it to fail fast rather than fall back to `COPY`. But given the data problems, this is a second-order concern.

---

## Overall verdict

**DO NOT MERGE / DO NOT APPLY.** The migration is blocked on:

1. **47,308 orphan rows** — `ADD CONSTRAINT` fails (data facts in `schema.sql`).
2. **Missing `ON DELETE` clause in the face of a false cascade assumption** — `ArchiveOldOrders:41` breaks under default `RESTRICT` (`context.md` claim vs. `application-usage.md` reality).
3. **`ReconcileGateway:63` generates fresh orphans** — the constraint will fail in the gateway pipeline at runtime, not just once at migration time.
4. Rolling deploy ordering: app fixes for (2) and (3) must ship before or with the migration, and the migration must run in a window with no gateway reconciliation writing.

The developer's statement "orders are hard-deleted ... with a cascade" is the central misconception: there is no cascade, and adding one to match that sentence would be worse than the current state. Fix the two deletion/reconciliation code paths to match a **`RESTRICT`** FK, remediate the 47k orphans via an explicit ledger-owner decision, and then ship the FK with an explicit `ON DELETE RESTRICT` clause written into the SQL (not just implied by the default).