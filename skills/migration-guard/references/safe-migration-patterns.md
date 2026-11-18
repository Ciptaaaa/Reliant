# Safe Migration Patterns

Load this file when a finding needs a safer alternative, or when the migration touches a running
table with more than one application version in play.

The default answer to "is this migration safe?" on a live table is usually **not yet**. The patterns
below are the ones that let you ship the change in steps.

## 1. Expand and contract

The baseline pattern for any change that alters or removes existing structure. Three releases:

```
Release N     : expand    — add new structure, application writes both, reads old
Release N+1   : backfill  — populate new structure, verify, read new
Release N+2   : contract  — stop writing old, drop old structure
```

Concretely, renaming `users.phone` to `users.phone_number`:

```sql
-- Release N (additive only)
ALTER TABLE users ADD COLUMN phone_number text;
CREATE INDEX CONCURRENTLY users_phone_number_idx ON users (phone_number);
-- Application: write both columns, read phone.
```

```text
-- Release N+1
Backfill phone_number from phone in batches, verify counts match.
Application: write both, read phone_number.
```

```sql
-- Release N+2 (destructive, gated)
ALTER TABLE users DROP COLUMN phone;   -- only after every writer stopped using it
```

That is four deploy steps, but at no point does any running version break. Contrast with a single
`ALTER TABLE users RENAME COLUMN phone TO phone_number`, which breaks version N instantly.

**Why this is the first thing to suggest:** it turns a `BLOCKER` into a sequence of `LOW` steps
whose final step is still destructive but is now *provably* safe because nothing reads the old name.

## 2. Additive migration

Prefer adding over modifying:

- New column instead of retyping an existing one.
- New table instead of altering a shape that other code depends on.
- New index instead of dropping and recreating the same index with a different definition.
- New nullable column with a backfill rather than adding `NOT NULL` with a default to an existing
  column.

An additive migration is not automatically safe — adding a column to a MySQL 5.7 table is a rebuild,
and adding a column to a partitioned PostgreSQL table with defaults can still be costly — but it
does not break readers, so it is much easier to make safe.

## 3. Batched backfill

Never write an unbatched `UPDATE` that touches a large fraction of a table. It holds row locks for
the whole duration, bloats the table, generates a huge WAL/binlog, and on MySQL blocks writes for the
whole statement.

Two rules that make every backfill safe to stop and re-run:

- **Commit per batch.** One transaction per batch, never one transaction for the whole job. A single
  long transaction makes the lock cost identical to the unbatched version, and on PostgreSQL it holds
  back vacuum for its entire duration.
- **Idempotent predicate.** The `WHERE` clause must select only rows that still need work, so a run
  interrupted at batch 700 resumes at batch 701 without double-applying anything.

The SQL differs between engines and there is no portable form. Do not give one engine's syntax as a
generic pattern.

### PostgreSQL

PostgreSQL can run the loop inside one session without a client loop. `DO` is a block; each `UPDATE` is
its own statement and commits on its own because `DO` does not open a transaction by default.

```sql
DO $$
DECLARE
  batch_size int := 1000;
  affected   int;
BEGIN
  LOOP
    UPDATE orders o
       SET status = 'pending'
     WHERE o.status IS NULL
       AND o.id IN (
         SELECT id FROM orders WHERE status IS NULL ORDER BY id LIMIT batch_size
       );
    GET DIAGNOSTICS affected = ROW_COUNT;
    EXIT WHEN affected = 0;
    COMMIT;              -- no-op inside DO; kept for clarity if run from a client loop
    PERFORM pg_sleep(0.2);
  END LOOP;
END;
$$;
```

Two notes that change the answer in real projects:

- A `DO` block runs inside a **single implicit transaction** if the client sends it as one command with
  explicit `BEGIN`. Run it with autocommit (psql default) so each statement-commit stands, or drive the
  loop from the client instead.
- For a very large table, prefer a client loop over `\gexec`, which gives you a commit point, a pause,
  and an abort condition on every iteration:

```sql
-- psql, repeating until 0 rows
SELECT format(
  'UPDATE orders SET status = %L WHERE id IN (SELECT id FROM orders WHERE status IS NULL ORDER BY id LIMIT 1000);',
  'pending'
)
WHERE EXISTS (SELECT 1 FROM orders WHERE status IS NULL) \gexec
```

### MySQL

MySQL has no anonymous block and writing to a table while selecting from it in the same statement can
fail with `ERROR 1093 (You can't specify target table for update in FROM clause)`. Do **not** ship the
`UPDATE ... WHERE id IN (SELECT ... FROM same_table ... LIMIT)` form: on many versions it is rejected
or the `LIMIT` is not accepted inside an `IN` subquery.

Select the batch ids first, then update by explicit id list. Each statement autocommits, which is what
you want per batch.

```sql
-- 1. Fetch the next batch (commit already happened; this is a plain read).
SELECT id FROM orders WHERE status IS NULL ORDER BY id LIMIT 1000;

-- 2. Update exactly those ids. Repeat with the next batch's ids.
UPDATE orders SET status = 'pending'
 WHERE status IS NULL
   AND id IN (/* the ids returned by step 1 */);
```

Drive steps 1 and 2 from the client until the read returns no rows. A stored procedure does this too,
but it hides the commit points from the reviewer; a client loop is easier to audit and to interrupt.

### Stop conditions (both engines)

A backfill without an abort condition is an outage waiting for a slow replica. The report must state:

| Condition | Action |
|---|---|
| Replication lag above a threshold (e.g. > 30 s or > 2× baseline) | Pause; resume when lag recovers |
| Batch duration above a threshold (e.g. 2× the first batch) | Pause and investigate lock contention |
| Error rate or failed batches above zero | Stop; do not retry blindly |
| Rows updated outperform the projection | Stop; the predicate is probably wrong |
| A human kill switch | Always available; the job must leave the table in a valid partial state |

### Rules the report must state

- **Batch size** (start 1000–5000; smaller for wide rows or hot tables), and why.
- **Pause between batches** (100–500 ms) so replication and autovacuum keep up.
- **Idempotent predicate**: re-running is safe after a crash.
- **Progress metric**: a count query for remaining rows.
- **Throttling by replication lag** if replicas exist; otherwise lag grows unbounded.
- **Not inside one transaction** for the whole job.

**Never** treat "I will backfill the NULLs" as the mitigation. Specify the loop, the commit point per
batch, the abort conditions, and its verification: `SELECT count(*) FROM t WHERE col IS NULL` must
return 0 before `SET NOT NULL`.

## 4. Dual read / dual write

Needed when the new structure must stay correct while old code still runs. This is the hardest
pattern; use it only when the additive step alone would produce wrong data (for example, splitting a
column into two columns used by a new query path).

- **Dual write:** every code path that modifies the old column also modifies the new one, inside the
  same transaction.
- **Dual read:** new code reads the new column; if it is `NULL`, fall back to the old one. Remove the
  fallback in the release after the backfill is verified.
- **Reconciliation job:** a query that finds rows where old and new disagree. Run it, fix the
  mismatches, and only then stop dual-writing.

Report this as `MEDIUM` (coordination) plus the contract step as destructive. Do not call it a
"temporary hack"; state when the old path will be removed.

## 5. Validate data before adding a constraint

Never add a constraint and hope. Prove the data first, in this order:

```sql
-- 1. Prove no violation exists.
SELECT count(*) FROM users WHERE email IS NULL;                     -- for NOT NULL
SELECT email, count(*) FROM users GROUP BY email HAVING count(*) > 1; -- for UNIQUE
SELECT count(*) FROM orders o
  LEFT JOIN customers c ON c.id = o.customer_id
 WHERE o.customer_id IS NOT NULL AND c.id IS NULL;                   -- for FK

-- 2. Fix what is found (batched, below).
-- 3. Add the constraint (NOT VALID where the engine supports it).
-- 4. Validate it.
```

On MySQL there is no `NOT VALID` equivalent for `CHECK` (8.0.16+) or for `FOREIGN KEY`; adding the
constraint validates at creation time. The safe approach there is the same: prove the data first,
schedule the `ALTER` with an explicit `LOCK=SHARED` when the algorithm supports it, and run it in
a low-traffic window.

## 6. Index creation strategy

| Engine | Safe online path |
|---|---|
| PostgreSQL | `CREATE INDEX CONCURRENTLY` outside any transaction; verify `indisvalid`; drop and rebuild if invalid |
| MySQL 8.0 | `ALTER TABLE t ADD INDEX i (...), ALGORITHM=INPLACE, LOCK=NONE` — secondary indexes support concurrent DML |
| MySQL 5.7 | `ALGORITHM=INPLACE, LOCK=NONE` — supported for secondary indexes, no `INSTANT` exists |
| MySQL unique index | Same in-place form; the documentation does not state a required lock level, so name the algorithm and check the server's answer on staging |

For both engines:

- `DROP INDEX` on PostgreSQL takes `ACCESS EXCLUSIVE` on the table. Use
  `DROP INDEX CONCURRENTLY` (PG 9.2+) to avoid the block.
- On MySQL, `DROP INDEX` for a secondary index is in-place and online.
- Do not create an index and immediately run the query that needs it in the same migration if you
  care about the plan; statistics may lag, and on PostgreSQL a concurrent build does not take a
  blocking lock but the index may not be usable until valid.
- An index on a large table is proportional to table size: budget disk and time; a failed
  `CONCURRENTLY` leaves an invalid index that must be cleaned up.

## 7. Delayed destructive migration

All destructive steps go last, in their own migration, after a verification gate:

```text
1. Deploy additive change.
2. Backfill.
3. Verify (counts, reconciliation query, error logs).
4. Deploy application that no longer references the old structure.
5. Wait one full deploy cycle (or a callable retention window for external consumers).
6. Drop the old structure, with backup and an approval to accept risk.
```

The report must state explicitly: the drop is safe **only because** steps 3–5 are complete. If any
of them is not, the drop is `BLOCKER`.

## 8. Migration shape rules

Structural rules that prevent whole classes of failure:

- **One concern per migration.** A migration that adds a column, backfills, and drops another column
  cannot be partially rolled back.
- **Keep DDL and data changes in separate migrations.** On MySQL they cannot share a transaction
  anyway; on PostgreSQL a failed DML inside the DDL transaction rolls the whole thing back, which
  is safer but still means a single long transaction.
- **No `CASCADE`, ever, in a reviewed migration.** Enumerate the dependent objects and drop them
  explicitly.
- **No `IF EXISTS` / `IF NOT EXISTS` as a safety net.** It hides drift.
- **Explicit transaction boundary.** For PostgreSQL, count the statements that cannot run in a
  transaction (`CONCURRENTLY`) and mark the migration accordingly.
- **Idempotency is not the same as safety.** Making a migration re-runnable does not make it
  non-destructive.
