# MY-004 — Expected analysis

## Expected verdict

**BLOCKED** — 47,308 orphan rows make the FK fail; and the FK changes the behaviour of two existing
deletion paths.

## Findings

### F1 — `BLOCKER` — `foreign-key` — orphan `order_id` values

`schema.sql` records 47,308 payments whose `order_id` has no matching order (2021 re-keying). MySQL
validates existing rows when adding the FK and fails with
`ERROR 1452 (23000): Cannot add or update a child row: a foreign key constraint fails`. There is no
`NOT VALID` equivalent in MySQL, so the data must be fixed **before** the `ALTER`.

**Evidence required:** `schema.sql` (47,308), `migration.sql`, `ReconcileGateway.php:63` (a writer that
can create more orphans).

**Mitigation:**

1. Decide the treatment with the finance owner: re-link to the re-keyed order, move to an
   `orphaned_payments` table, or (with approval) delete. This is a data decision.
2. Backfill/relink in batches; run the orphan-detection query until it returns 0.
3. Fix `ReconcileGateway.php:63` to verify the order exists (or skip/queue the row), otherwise new
   orphans reappear and the FK writes begin failing at runtime.
4. Add the FK — see F4 for the lock clause and F2 for the delete semantics.

**Approval: accept-risk (F1)** — a human accepts the loss of the old values; a recovery plan and restore point are required.

### F2 — `HIGH` — `application-compatibility` — default FK action breaks order archival

`app/Services/Orders/ArchiveOldOrders.php:41` deletes order rows without deleting or moving payments.
With the FK defaulting to `RESTRICT`, archiving a single order that has payments now fails with
`ERROR 1451 (Cannot delete or update a parent row)`. The developer's claim "orders are deleted with a
cascade" is false: `DeleteOrder.php:28` deletes payments manually, and the archive path does not.
`DELETE`/`NO ACTION` semantics must be decided explicitly (`CASCADE`, `RESTRICT` + code change, or
archive payments too) and matched in the application.

**Evidence required:** `application-usage.md` (ArchiveOldOrders, DeleteOrder).

**Mitigation:** update `ArchiveOldOrders` to move/delete payments alongside the order in one
transaction, and choose the FK action deliberately.

### F4 — `HIGH` — `lock-and-rewrite` — adding an FK is a blocking `COPY` unless `foreign_key_checks=0`

Adding a foreign key is **not** a generally online operation on MySQL. With `foreign_key_checks` at its
default (enabled), the manual states that `ALGORITHM=INPLACE` is supported only when
`foreign_key_checks=0`; otherwise only `ALGORITHM=COPY` is available. `COPY` rebuilds the table and
blocks all DML for the duration — on 11M rows that is a write outage on `payments`.

The migration names no algorithm, so MySQL will take the `COPY` path when the data is clean.

**Evidence required:** `migration.sql`, row count in `schema.sql`, `context.md` (no algorithm stated).

**Mitigation:** measure on a staging copy of production size. Options: run the FK add in a window with
`ALGORITHM=COPY` (and a verified duration), or use a tool that performs online schema changes, or add
the FK on a new shadow table with `CREATE TABLE … LIKE`/`ALTER` before swapping. Choose deliberately;
do not promise online.

## Unknowns that must be reported

- The gateway settlement file may continue to reference non-existent orders.
- Whether `orders_archive` is intended to be the long-term owner of payments is a product decision.
- No staging measurement of the FK's copy duration is provided.
- Whether `foreign_key_checks` is disabled anywhere in the deploy pipeline is unknown.

## Verdict reasoning

Orphans + FK = certain failure → `BLOCKER` → `BLOCKED`. Even after the data is fixed, the add is a
blocking `COPY` (F4).
