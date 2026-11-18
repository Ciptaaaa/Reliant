# PG-002 — Expected analysis

## Expected verdict

**CONDITIONAL** — the statement will fail on existing data and needs the safe `NOT VALID` sequence
plus a decision about writes that still insert `status = null`.

## Findings

### F1 — `HIGH` — `not-null-add` — `SET NOT NULL` will fail because 36,117 rows are `NULL`

`schema.sql` records `SELECT count(*) FROM orders WHERE status IS NULL` → 36,117 (out of 4.8M rows).
`ALTER TABLE orders ALTER COLUMN status SET NOT NULL` validates the whole table and **fails** with
`column "status" of relation "orders" contains null values`. The deploy would then roll back; with
Prisma the whole migration transaction is aborted, so nothing is half-applied — but the release fails.

Even if the NULLs were cleaned, the statement takes `ACCESS EXCLUSIVE` and scans 4.8M rows, blocking
reads and writes for the duration.

**Evidence required:** `schema.sql` (NULL count), `migration.sql:2`.

**Mitigation:** the safe 4-step PostgreSQL sequence:

```sql
ALTER TABLE orders ADD CONSTRAINT orders_status_not_null CHECK (status IS NOT NULL) NOT VALID;
ALTER TABLE orders VALIDATE CONSTRAINT orders_status_not_null;   -- SHARE UPDATE EXCLUSIVE
ALTER TABLE orders ALTER COLUMN status SET NOT NULL;             -- fast, proven by the check
ALTER TABLE orders DROP CONSTRAINT orders_status_not_null;
```

preceded by a **batched** backfill of the 36,117 NULLs and the code change in
`src/domain/orders/create.ts:18` so new rows never insert `NULL`.
### F2 — `MEDIUM` — `application-compatibility` — writers still insert `NULL`

`src/domain/orders/create.ts:18` creates orders with `status: null`, and `src/imports/legacy-shop.ts:12`
inserts rows without `status`. Once `NOT NULL` is enforced (or even once the `NOT VALID` check is
added — it is enforced for *new* rows immediately), both paths start failing with a constraint
violation.

**Evidence required:** `application-usage.md` (create.ts and legacy importer).

**Mitigation:** change order creation to write a real initial status (`'pending'`) **before** the
constraint is added; retire or adapt the legacy importer. Also decide the backfill value for the NULL
rows, and confirm with finance that `'unknown'` vs `'fulfilled'` is correct.

### F3 — `MEDIUM` — `lock-and-rewrite` — full-table validation under `ACCESS EXCLUSIVE`

The plain `SET NOT NULL` scans 4.8M rows while holding `ACCESS EXCLUSIVE`. At 400 inserts/min plus
reads, that is a write and read stall; the migration also waits behind any long transaction and
queues everyone else behind it.

**Mitigation:** use `VALIDATE CONSTRAINT`, which only takes `SHARE UPDATE EXCLUSIVE`; set a short
`lock_timeout` for the final `SET NOT NULL`.

### F4 — `INFO` — `no-risk-observed` — backfill is safe when batched

The backfill itself (`UPDATE orders SET status = 'pending' WHERE status IS NULL` in batches of ~1000)
is additive and reversible (the original value was `NULL`); it does not change the column type or
drop data.

## Unknowns that must be reported

- The fulfilment worker version is not known; if it is older than the API it may still write `NULL`
  or rely on `NULL` status.
- The correct backfill value for historical rows is a product decision, not an engineering one.

## Verdict reasoning

No `BLOCKER`: the migration fails loudly rather than destroying data, and the NULL count is known.
One `HIGH` (the statement cannot succeed as written, and its blocking form is costly) → `CONDITIONAL`.
A report that says `APPROVED` because "the SQL is fine for new orders" is wrong. A report that
classifies this as `BLOCKER` is defensible only if it argues the deploy failure itself blocks the
release; the fixture treats it as `HIGH` because the failure is safe (no data corruption).
