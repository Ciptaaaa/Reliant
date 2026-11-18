# PG-006 — Expected analysis

## Expected verdict

**CONDITIONAL** — the statement as written fails because the migration runner wraps it in a
transaction; it is safe once the transaction wrapper is removed and the invalid-index risk is handled.

## Findings

### F1 — `HIGH` — `index-build` — `CONCURRENTLY` cannot run inside a transaction block

`context.md` states that Rails wraps each migration in `BEGIN … COMMIT`, and the migration has no
`disable_ddl_transaction!`. PostgreSQL rejects `CREATE INDEX CONCURRENTLY` with
`CREATE INDEX CONCURRENTLY cannot run inside a transaction block`. The deploy fails on this migration.

**Evidence required:** `migration.sql` (the `CONCURRENTLY` statement plus the comment that no
`disable_ddl_transaction!` exists), `context.md` (transaction wrapper).

**Mitigation:** add `disable_ddl_transaction!` to the migration class (or the framework equivalent for
non-Rails runners), or run it out-of-band with `psql -1` omitted. Verify with a migration smoke test
on a staging database.

**Human approval required:** no — additive index build, no data change.

### F2 — `HIGH` — `index-build` — a failed concurrent build leaves an `INVALID` index

`CREATE INDEX CONCURRENTLY` has two table scans and can fail partway (lock timeout, a constraint
violation on a unique build, cancellation). The resulting index exists but is invalid
(`pg_index.indisvalid = false`): queries ignore it while every write still maintains it.

**Evidence required:** `migration.sql`.

**Mitigation:** after the build, check
`SELECT indisvalid FROM pg_index WHERE indexrelid = 'orders_status_idx'::regclass;`. If `false`,
`DROP INDEX CONCURRENTLY orders_status_idx;` and retry. Add this check to the pre-deploy checklist.

### F3 — `LOW` — `lock-and-rewrite` — no write blocking while building (when run correctly)

Run outside a transaction, `CONCURRENTLY` does not take a blocking lock on `orders`; it waits for
existing transactions to finish but does not stop new reads or writes. On a 9M-row table the build is
long but non-blocking.

**Mitigation:** none needed beyond running it correctly; note that it waits on long-running
transactions.

### F4 — `INFO` — `no-risk-observed` — the index is actually needed

`app/controllers/orders_controller.rb:18` filters by `status` and no existing index covers it
(`schema.sql` has only `orders_customer_id_idx`). Adding the index is justified by usage evidence.

## Unknowns that must be reported

- Whether long-running transactions (reporting) could hold the concurrent build open for a long time
  is not stated.

## Verdict reasoning

The migration cannot deploy as written (`HIGH`), but nothing is destroyed and the fix is small →
`CONDITIONAL`. `BLOCKED` is too strong; `APPROVED` misses that the deploy fails.
