# Migration Guard Report — MY-004

**Migration under review:** `database/migrations/2026_11_16_123000_payments_order_fk.php` (raw SQL form)

```sql
ALTER TABLE `payments`
  ADD CONSTRAINT `payments_order_id_fk` FOREIGN KEY (`order_id`) REFERENCES `orders` (`id`);
```

**Verdict: BLOCK — do not merge.**

Two blocking issues. The first makes the migration fail on deploy; the second makes it succeed but silently change production delete/archive semantics in a way the code does not support.

---

## Blocking issue 1 — Orphan rows will cause `ADD CONSTRAINT` to fail

The developer's premise ("this should be consistent") is contradicted by their own data counts in `schema.sql`:

> `SELECT COUNT(*) FROM payments p LEFT JOIN orders o ON o.id = p.order_id WHERE o.id IS NULL;` → **47,308 — orphans**
>
> "Orphans come from a 2021 migration that copied orders to a new table and re-keyed ids."

MySQL 8.0 InnoDB has no `NOT VALID` option for `FOREIGN KEY`; `ADD CONSTRAINT` performs an immediate full-table validation scan and will abort with `errno 1452` (Cannot add or update a child row) given any orphan. With 11,204,882 payment rows and 47,308 known bad `order_id` values, this `ALTER` **cannot succeed as written**. It will also run a validation scan over ~11M rows while holding the table — on a rolling deploy that is a metadata-lock + full-scan hit, not an instant DDL.

Blocker. The migration must first reconcile or quarantine the 47,308 orphans (e.g. backfill correct ids, or move to a `payments_orphaned` table / nullable `order_id` parking column), *then* add the FK in a separate migration after verifying count = 0.

## Blocking issue 2 — No `ON DELETE` clause changes existing delete/archive behavior

The proposed FK omits `ON DELETE`, so the action defaults to `RESTRICT`/`NO ACTION`. The developer's claim "Orders are hard-deleted by the admin panel with a cascade" is **not what the code does**. From `application-usage.md`:

> `app/Services/Orders/DeleteOrder.php:28`
> ```php
> $order->payments()->delete();   // delete payments first
> $order->forceDelete();          // then the order
> ```
> "There is no `ON DELETE CASCADE` anywhere."

So the current admin delete path happens to satisfy `RESTRICT` (children removed first). But:

> `app/Services/Orders/ArchiveOldOrders.php:41` — "moves old orders to `orders_archive` and deletes from `orders` **without** deleting payments (they keep pointing at the archived ids). This path breaks under an FK."

And the report is explicit about the class of failure this introduces:

> "**any** code path that deletes an order without first deleting payments starts failing with `ERROR 1451 (Cannot delete or update a parent row)`."

The archive job is exactly such a path. Merging this FK will turn a currently-working archive service into a runtime `ERROR 1451` failure. The application reading (`Order.php:34`, report screens) also assumes referential integrity the UI never had to worry about before — but the write paths are the concrete breakage.

Blocker. The migration must declare an explicit `ON DELETE` action consistent with the actual code, and the archive path must be fixed in the same change (or archive via FK-safe means). Given `ArchiveOldOrders` intentionally *keeps* payments pointing at archived ids, `ON DELETE CASCADE` is wrong here — it would silently destroy payment history on archive. `RESTRICT` is the semantic the archive job currently violates; the safer sequencing is: fix `ArchiveOldOrders` to move payments with the orders (or stop hard-deleting) before any FK is added, and choose `ON DELETE` deliberately with the DBA/product owner.

## Additional risks (non-blocking, but must be addressed)

1. **Index presence on `orders.id`:** The FK requires an index on the referenced column. `orders.id` is the primary key (`PRIMARY KEY (id)`), so this is satisfied. `payments.order_id` already has `KEY payments_order_id_idx (order_id)`, so the FK will reuse it rather than build a new one — good, but confirm no duplicate index is created.

2. **Column type compatibility:** Both sides are `BIGINT UNSIGNED` per `schema.sql`, so the type check passes. (Context notes the fixture is designed to test orphans, not type mismatch — confirmed compatible.)

3. **Gateway reconcile path (runtime integrity):** `app/Console/Commands/ReconcileGateway.php:63` "inserts payments from a gateway settlement file, keyed by `order_id` taken from the file; does **not** check that the order exists locally." After this FK lands, any settlement row whose `order_id` has no local order will fail with `ERROR 1452` at insert time. This is *probably* desirable (it surfaces bad gateway data), but it is a behavioral change on a background command and should be handled explicitly — validate before insert, or dead-letter bad rows — rather than relying on an unhandled DB exception.

4. **Deployment safety:** `ALTER TABLE ... ADD FOREIGN KEY` on InnoDB validates the whole child table and takes a metadata lock; on 11M rows this is not `ALGORITHM=INSTANT`. Plan it as an off-peak online-DDL step (pt-online-schema-change / gh-ost, or a maintenance window), not part of the normal rolling deploy.

## Required changes before merge

1. **Reconcile the 47,308 orphans first** in a separate, idempotent data migration; verify `orphan count = 0` before the FK migration runs. Gate the FK migration on that check (or split into `reconcile` → `verify` → `add_fk`).
2. **Fix `ArchiveOldOrders`** so order rows are not hard-deleted while payments retain their `order_id` — run the archive and FK in the same release only after this is corrected.
3. **Add an explicit `ON DELETE` action** to the constraint and justify it against every delete path (`DeleteOrder`, `ArchiveOldOrders`, admin panel). Add a regression test that exercises archive + delete under the FK.
4. **Handle the reconcile insert path** so missing local orders produce a controlled failure, not an unhandled `ERROR 1452`.
5. **Schedule the DDL** as online schema change / off-peak, with a rollback plan (`DROP FOREIGN KEY payments_order_id_fk`).

---

**Bottom line:** The migration fails on deploy due to 47,308 orphan rows (evidence: `schema.sql` data facts), and even after fixing that, it introduces `ERROR 1451` failures in `ArchiveOldOrders` because there is no `ON DELETE CASCADE` anywhere in the codebase (evidence: `application-usage.md`). **BLOCK.**