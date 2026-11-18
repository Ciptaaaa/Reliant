# Migration Guard Report

## Scope
- **Migration**: `database/migrations/2026_11_16_124000_invoices_currency_varchar.php`
- **Engine**: MySQL 8.0.36 (InnoDB)
- **Inspected**: `migration.sql`, `schema.sql`, `application-usage.md`, `context.md`
- **Not inspected**: the partner accounting system that receives invoice PDFs (external)
- **Unknowns**: whether this specific ALTER has ever been staged; `context.md:13` says it has not

## Verdict
**CONDITIONAL** — `ALGORITHM=INSTANT` does not cover a column type change, so MySQL rejects the statement and the deploy fails.

## Findings

### F1 — HIGH — lock-and-rewrite
- **Statement**: `ALTER TABLE invoices MODIFY COLUMN currency VARCHAR(3) NOT NULL, ALGORITHM=INSTANT, LOCK=NONE` in `migration.sql:2-3` — changes a column's data type while requesting an algorithm that cannot perform it
- **Evidence**:
  - `migration.sql:2-3` — `MODIFY COLUMN currency CHAR(3) -> VARCHAR(3)` with `ALGORITHM=INSTANT, LOCK=NONE`
  - `schema.sql:9` — `currency CHAR(3) NOT NULL`; the change is a type change, not a column add, drop, or rename
  - `context.md:24` — `ALGORITHM=INSTANT` is only supported for a specific and evolving set of operations, and a column type change is not one of them
- **Impact**: MySQL rejects the statement because the requested algorithm does not support a type change, so the migration step fails and no later task starts; the statement errors out rather than silently copying the table
- **Mitigation**: state the algorithm the operation actually supports and verify it on staging with `SHOW WARNINGS`; a `CHAR(3)` to `VARCHAR(3)` change is a table copy on this version, so it needs a window or a shadow-column approach
- **Verification**: run the statement on a staging copy and read `SHOW WARNINGS`; if it reports an algorithm escalation, treat the production run as a rebuild
- **Approval**: schedule
- **Reversible**: yes

### F2 — HIGH — lock-and-rewrite
- **Statement**: without `INSTANT`, this type change copies or rebuilds all 18,400,000 rows, blocking writes on the hot invoice path
- **Evidence**:
  - `schema.sql:16` — `invoices` holds ~18,400,000 rows
  - `context.md:11` — "~18M rows; hot write path (every order creates an invoice)"
  - `migration.sql:2-3` — the request assumes a metadata change, so no window was planned
- **Impact**: on 18.4M rows under a hot write path the copy blocks DML for its duration and can exhaust disk while it runs, which is an outage rather than a deploy detail
- **Mitigation**: use a shadow column — add `currency_v` `VARCHAR(3)` online, backfill in batches with an idempotent predicate, swap under a short lock — or schedule a maintenance window and accept a bounded write pause
- **Verification**: during the backfill invoice inserts continue; the final swap is measured in seconds, not minutes
- **Approval**: schedule
- **Reversible**: yes

## Safer migration path
1. Confirm what MySQL does on staging, because the requested algorithm cannot apply:
   ```sql
   ALTER TABLE invoices MODIFY COLUMN currency VARCHAR(3) NOT NULL;
   SHOW WARNINGS;
   ```
2. If the server reports a copy, prefer the shadow column:
   ```sql
   ALTER TABLE invoices ADD COLUMN currency_v VARCHAR(3) NOT NULL DEFAULT 'USD', ALGORITHM=INSTANT, LOCK=DEFAULT;
   -- backfill currency_v from currency in batches; then swap under a brief lock
   ```
3. Run inside a window with `lock_wait_timeout` set so the change fails fast on a busy moment.
4. Verify `SHOW COLUMNS FROM invoices` shows `VARCHAR(3)` and the reported lock wait is within the agreed bound.

## Rollback and recovery
- **Schema rollback**: `ALTER TABLE invoices MODIFY COLUMN currency CHAR(3) NOT NULL;` restores the type, and it is another copy on this version, so it costs the same as the forward change.
- **Data recovery**: no value changes, so no recovery is needed; the risk is operational only.
- **Application rollback**: safe — the API returns `currency` as a string either way (`application-usage.md:11`) and the PDF prints it (`application-usage.md:12`).

## Pre-deploy checklist
- [ ] Staging run executed and `SHOW WARNINGS` read
- [ ] Window approved for the copy, or the shadow-column path chosen
- [ ] `lock_wait_timeout` set for the migration session
- [ ] Replica lag monitored during the change