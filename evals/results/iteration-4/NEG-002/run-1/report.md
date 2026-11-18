# Migration Guard Report

## Scope
- **Migration**: `db/migrate/20261116111000_add_orders_customer_placed_idx.py`
- **Engine**: PostgreSQL 16
- **Inspected**: `migration.sql`, `schema.sql`, `application-usage.md`, `context.md`
- **Not inspected**: the reporting dashboards that may also query `orders`
- **Unknowns**: none material; the migration is non-transactional as required and the table size is stated

## Verdict
**APPROVED** — the index is built concurrently for a query that exists, the migration correctly runs outside a transaction, and no write path is blocked.

> APPROVED means no blocker was found in the inspected scope with the evidence available. This is not a
> production guarantee.

## Findings

### F1 — LOW — index-build
- **Statement**: `CREATE INDEX CONCURRENTLY orders_customer_placed_idx ON orders (customer_id, placed_at DESC)` in `migration.sql:10` — builds a composite index without blocking writes
- **Evidence**:
  - `migration.sql:10` — `CREATE INDEX CONCURRENTLY`, and the header sets `atomic = False`, which is what permits `CONCURRENTLY`
  - `application-usage.md:4` — `app/orders/views.py:41` runs `filter(customer_id=cid).order_by("-placed_at")[:50]`, the query this index targets
  - `schema.sql:13` — ~9,000,000 rows; only `orders_customer_id_idx` exists today
- **Impact**: the build is long on 9M rows but does not block reads or writes; it takes two passes over the table and adds I/O
- **Mitigation**: run in a quiet period and keep `statement_timeout` generous, because a cancelled concurrent build leaves an `INVALID` index
- **Verification**: `SELECT indisvalid FROM pg_index WHERE indexrelid = 'orders_customer_placed_idx'::regclass` returns true, and `EXPLAIN` for the query at `app/orders/views.py:41` shows an index scan
- **Approval**: none
- **Reversible**: yes

### F2 — INFO — no-risk-observed
- **Statement**: checked the two failure modes a concurrent index usually has, and neither applies here
- **Evidence**:
  - `migration.sql:4` — `atomic = False` is set, so the statement is not wrapped in a transaction block
  - `application-usage.md:11` — no migration drops an index on `orders`, and no code depends on the index name
- **Impact**: none. This is the additive, non-blocking form of an index build.
- **Mitigation**: none
- **Verification**: no `INVALID` index remains after the build
- **Approval**: none
- **Reversible**: yes

## Safer migration path
Not needed. The migration already names `CONCURRENTLY` and runs outside a transaction, which is the safe form.

## Rollback and recovery
- **Schema rollback**: `DROP INDEX CONCURRENTLY orders_customer_placed_idx;` — non-blocking and clean.
- **Data recovery**: not applicable; an index build changes no row data.
- **Application rollback**: safe — the index only changes query plans, and no code depends on its existence.

## Pre-deploy checklist
- [ ] Confirmed the migration is non-transactional in the actual runner, not only in the header comment
- [ ] Build scheduled outside peak hours
- [ ] `indisvalid` checked after the build