# NEG-002 — Expected analysis

## Expected verdict

**APPROVED** — an additive, non-blocking index build on a column pair the application uses.

## Findings

### F1 — `LOW` — `index-build` — long but non-blocking concurrent build

`CREATE INDEX CONCURRENTLY orders_customer_placed_idx …` on 9M rows does not take a blocking lock: reads
and writes continue while it scans. It is marked non-transactional (`atomic = False`), which is the
required condition — `CREATE INDEX CONCURRENTLY` cannot run inside a transaction block, and this
migration satisfies that. It may wait behind long-running transactions and takes two table scans, so
budget time and CPU.

**Evidence required:** `migration.sql` (`CONCURRENTLY` plus `atomic = False`), `schema.sql`.

**Mitigation:** none required. Optional: after completion, confirm
`SELECT indisvalid FROM pg_index WHERE indexrelid = 'orders_customer_placed_idx'::regclass;` is `true`.
If a build fails partway it leaves an invalid index that must be dropped and rebuilt.

### F2 — `INFO` — `no-risk-observed` — index is justified and purely additive

`app/orders/views.py:41` filters by `customer_id` and orders by `placed_at`; the composite index matches
that access pattern. No data, constraint, or existing index is touched. Old and new code both work.

**Approval: none** — every step in this migration is reversible without a backup.

## Unknowns that must be reported

- Long-running reporting transactions could delay the build but do not block writes (optional note).

## Verdict reasoning

`LOW` only, evidence present → `APPROVED`. Reporting this as write-blocking, transactional, or as a
`HIGH`/`MEDIUM` risk is a false positive.

## This is a negative case

Any `HIGH`+ finding, any claim of blocking writes, or a non-`APPROVED` verdict counts as a false
positive / verdict miss.
