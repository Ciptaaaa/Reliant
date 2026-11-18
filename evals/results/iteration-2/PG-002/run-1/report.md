# Migration Guard Report — PG-002

**Migration:** `prisma/migrations/20261116091500_orders_status_not_null/migration.sql`
**Statement:** `ALTER TABLE orders ALTER COLUMN status SET NOT NULL;`
**Verdict: 🔴 BLOCK — do not merge as written.**

---

## 1. Will it even succeed?

No. There are existing `NULL` values, and `ALTER TABLE ... SET NOT NULL` rejects any row that is null.

Evidence (`schema.sql`):
> `SELECT count(*) FROM orders WHERE status IS NULL; -> 36,117`

The developer's claim — *"The column already has values for all new orders"* — is irrelevant to existing data. The 36,117 NULL rows are historical imports:

> `-- The NULL rows are historical imports from the legacy shop system (before 2023-06).`

Because the deploy step applies the migration "inside a single transaction," the `ALTER` fails on those rows, and the transaction rolls back. In practice, this migration breaks the deploy.

## 2. Locking and scan cost

Even if the table had no NULLs, this statement would be unsafe for a live, rolling deploy:

- `SET NOT NULL` takes an **`ACCESS EXCLUSIVE`** lock on `orders`. It blocks all reads and writes, including the list/API read path and the ~400 inserts/min peak traffic (`context.md`).
- To prove validity, PostgreSQL **scans the entire table** under that lock so that it can find any NULL. On 4,812,004 rows this means a full table scan while writes are blocked: an outage window, with no maintenance window available (`context.md`: *"No maintenance window. Migration runs automatically as part of the deploy."*).
- The `orders_status_idx` index is not used to skip the scan; the constraint check is not satisfied by an index in this form.

## 3. Downstream breakage this change would introduce (must be resolved before NOT NULL)

The schema change must be reconciled with every current writer; otherwise the deploy only fails again once it is pushed:

- **`src/domain/orders/create.ts:18`** inserts `status: null` on every new order:
  > `await prisma.order.create({ data: { customerId, status: null, totalCents } });`
  > → **new orders are inserted with `status = null` until the fulfilment worker sets it.**

  That fails immediately after the NOT NULL constraint is applied. New orders must get a real default status (that is, an application change and possibly a schema default), or inserts must be sequenced differently.
- **`src/imports/legacy-shop.ts:12`** inserts rows without `status`. This also fails once the constraint is applied. The importer is not retired:
  > Finance … "is expected to keep producing rows with `NULL` status until the importer is retired."
- **`src/api/orders/report.ts:41`** relies on a **`null` bucket** in the dashboard. Applying NOT NULL silently drops that bucket. The finance consumer also **"treats `NULL` as 'unknown'"** and still expects NULL rows.
- **Deployment version skew:** the fulfilment worker *"may lag behind the API"* (not searched). A lagging worker plus a new constraint is another failure mode that the team has not reviewed.
- The developer's statement *"The column already has values for all new orders"* contradicts the code at `create.ts:18`. That contradiction alone undermines the premise of the change.

**Implication:** making the column NOT NULL is not a migration-only change. It requires coordinated changes to inserts (default status), the importer, the report's null bucket, and the finance consumer's contract. None of these are in this PR.

## 4. Required approach (if this change is still wanted)

Do not merge a bare `SET NOT NULL`. The safe sequence is:

1. **Decide the data as well as the contract.** Either backfill the 36,117 historical rows to a real status (agree with finance on the mapping from "unknown" to a value), or keep them nullable and abandon NOT NULL. Separately, add `DEFAULT 'pending'` (or whatever value each team agrees on) and change `create.ts` and the legacy importer to stop writing NULL, then deploy that before the constraint.

2. **Add the constraint as `NOT VALID` first** (short lock, no scan):
   ```sql
   ALTER TABLE orders ADD CONSTRAINT orders_status_not_null CHECK (status IS NOT NULL) NOT VALID;
   ```
3. **Validate it** in a separate migration or step outside the API deploy transaction, so that a long `VALIDATE CONSTRAINT` does not block writes:
   ```sql
   ALTER TABLE orders VALIDATE CONSTRAINT orders_status_not_null;
   ```
   `VALIDATE` takes only `SHARE UPDATE EXCLUSIVE`, so writes continue.
4. **Promote it** (PG 12+): once the check is validated, `SET NOT NULL` skips the full scan, but it still takes `ACCESS EXCLUSIVE`, so it must run briefly and separately from the long step:
   ```sql
   ALTER TABLE orders ALTER COLUMN status SET NOT NULL;
   ALTER TABLE orders DROP CONSTRAINT orders_status_not_null;
   ```
5. **Sequence the rollouts:** first update the writers (create, importer), then backfill, then run the `CHECK NOT VALID` → `VALIDATE` → `SET NOT NULL` steps. Do not run a constraint that contradicts what `create.ts` currently writes.

The developer's proposed one-liner is not shippable. It fails on the existing NULLs, and it would take an `ACCESS EXCLUSIVE` lock plus a full scan on 4.8M rows during live traffic even if there were no NULLs. The NOT NULL goal also requires application changes to `create.ts`, the legacy importer, and the finance contract before the constraint can go in.