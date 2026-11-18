# PostgreSQL Migration Safety

Load this file when the migration targets PostgreSQL and you need to decide lock level, rewrite
cost, or concurrency safety. Every claim here was checked against the PostgreSQL documentation;
version qualifiers are stated where behavior changed.

Primary sources:

- `ALTER TABLE`: <https://www.postgresql.org/docs/current/sql-altertable.html>
- Explicit locking: <https://www.postgresql.org/docs/current/explicit-locking.html>
- `CREATE INDEX`: <https://www.postgresql.org/docs/current/sql-createindex.html>
- Routine vacuuming / bloat: <https://www.postgresql.org/docs/current/routine-vacuuming.html>

## 1. The default lock is `ACCESS EXCLUSIVE`

> "An ACCESS EXCLUSIVE lock is acquired unless explicitly noted. When multiple subcommands are
> given, the lock acquired will be the strictest one required by any subcommand."

`ACCESS EXCLUSIVE` conflicts with **every** other lock mode, including `ACCESS SHARE` taken by plain
`SELECT`. Consequence: for the duration of the `ALTER TABLE`, reads of that table wait. If the
statement must also wait for an already-running long transaction on that table, the `ALTER TABLE`
sits in the lock queue and **every subsequent query on the table queues behind it** (lock pile-up).

That pile-up, not the rewrite alone, is what turns a "quick" `ALTER` into an outage. Mitigation
patterns for setting a short `lock_timeout` are in `safe-migration-patterns.md`.

Lock levels that are **not** `ACCESS EXCLUSIVE` (from the docs):

| Operation | Lock |
|---|---|
| `ADD FOREIGN KEY ...` (and on the referenced table) | `SHARE ROW EXCLUSIVE` (docs say only this one subform avoids `ACCESS EXCLUSIVE`) |
| `VALIDATE CONSTRAINT` | `SHARE UPDATE EXCLUSIVE` (+ `ROW SHARE` on the referenced table for FK) |
| `SET STATISTICS`, `SET (attribute_option)`, storage params (`fillfactor`, toast, autovacuum, `parallel_workers`) | `SHARE UPDATE EXCLUSIVE` |
| `ENABLE/DISABLE TRIGGER` | `SHARE ROW EXCLUSIVE` |
| `CLUSTER ON` / `SET WITHOUT CLUSTER` | `SHARE UPDATE EXCLUSIVE` |
| `DETACH PARTITION CONCURRENTLY` | `SHARE UPDATE EXCLUSIVE` on parent + partition |

Everything else — `ADD COLUMN`, `DROP COLUMN`, `ALTER COLUMN TYPE`, `SET NOT NULL`, `ADD CHECK`,
`ADD UNIQUE`, `ADD PRIMARY KEY`, `DROP CONSTRAINT` — takes `ACCESS EXCLUSIVE`.

**Review rule:** any statement not in the table above is a write-blocking statement. On a table that
receives traffic, that alone makes it at least `MEDIUM`, and `HIGH` if the statement scans or rewrites
a large table or if its duration is unknown.

## 2. Which operations rewrite the table

From the `Notes` section of `ALTER TABLE`:

| Operation | Rewrite? |
|---|---|
| `ADD COLUMN` with no default / `NULL` default | No — metadata only |
| `ADD COLUMN` with a **non-volatile** `DEFAULT` | No — value stored in metadata, applied on read |
| `ADD COLUMN` with a **volatile** default (`clock_timestamp()`, `random()`, …) | **Yes** — full rewrite incl. indexes |
| `ADD COLUMN` stored generated column, identity column, domain with constraints | **Yes** |
| `ADD COLUMN` virtual generated column | No |
| `ALTER COLUMN TYPE` | **Normally yes.** Exception: if the `USING` expression does not change contents and types are binary-coercible / unconstrained domain — no rewrite. Indexes are rebuilt unless provably equivalent (collation change forces a rebuild; `text` ↔ `varchar` does not). |
| `ADD CHECK` / `ADD NOT NULL` | No rewrite, **but** scans the whole table to verify existing rows |
| `DROP COLUMN` | **No rewrite.** It only makes the column invisible; disk space is reclaimed later. |
| `SET/DROP DEFAULT` | No — affects future inserts only |
| `SET TABLESPACE`, `SET ACCESS METHOD`, `SET LOGGED/UNLOGGED` | Rewrites |

Two facts worth stating explicitly in a report:

- A rewrite needs **up to twice the table's disk space** temporarily and rebuilds every index.
- **Rewrites are not MVCC-safe:** "After a table rewrite, the table will appear empty to concurrent
  transactions, if they are using a snapshot taken before the rewrite occurred." Long-running
  transactions that hold an old snapshot can therefore observe an empty table after the commit. This
  is a correctness risk on top of the lock risk.

## 3. `CREATE INDEX CONCURRENTLY`

- Builds the index without blocking writes (it does not take a blocking lock on the table), but it
  **cannot run inside a transaction block**.
- If it is launched from a migration framework that wraps every migration in a transaction, the
  migration fails outright. Check the framework's settings (for example, a "disable transaction"
  annotation) before reporting this as safe.
- A **failed** concurrent build leaves an `INVALID` index behind. Queries do not use it, writes still
  pay its maintenance cost, and it must be dropped and rebuilt. A report must tell the user to check
  `pg_index.indisvalid` afterwards.
- `CONCURRENTLY` requires two table scans and waits for all transactions that could see the table to
  finish. It can block for a long time on a busy database even though it does not block.
- `REINDEX INDEX CONCURRENTLY` exists (PG 12+) and is the online way to rebuild an index.

Unqualified `CREATE INDEX` takes `SHARE` lock: it allows `SELECT` but blocks writes for the whole
build. On a large table this is `HIGH`.

`DROP INDEX` (without `CONCURRENTLY`) takes `ACCESS EXCLUSIVE` on the **table**, not just the index —
dropping an index blocks reads and writes on the table. Use `DROP INDEX CONCURRENTLY` on PostgreSQL
9.2 and newer.

## 4. Adding `NOT NULL` safely

`SET NOT NULL` scans the entire table under `ACCESS EXCLUSIVE` unless a valid `CHECK` constraint
proves no `NULL` can exist (docs: "if a valid CHECK constraint exists (and is not dropped in the same
command) which proves no NULL can exist, then the table scan is skipped").

Safe sequence:

```sql
-- 1. Enforce for new rows without scanning existing ones.
ALTER TABLE orders ADD CONSTRAINT orders_status_not_null
  CHECK (status IS NOT NULL) NOT VALID;

-- 2. Verify existing rows without blocking writes (SHARE UPDATE EXCLUSIVE).
ALTER TABLE orders VALIDATE CONSTRAINT orders_status_not_null;

-- 3. Now the SET NOT NULL is fast because the check constraint proves it.
ALTER TABLE orders ALTER COLUMN status SET NOT NULL;

-- 4. Optional: the check constraint is now redundant.
ALTER TABLE orders DROP CONSTRAINT orders_status_not_null;
```

PostgreSQL 18+ also supports adding a `NOT NULL` constraint directly with `NOT VALID`
(`ALTER TABLE ... ADD CONSTRAINT ... NOT NULL ... NOT VALID`). On earlier versions use the `CHECK`
pattern above.

`NOT VALID` means: enforced for **new inserts/updates**, not yet verified for existing rows. A
`NOT VALID` constraint that is never validated is an incomplete migration, not a finished one — say
so in the report.

## 5. Adding foreign keys and unique constraints without long locks

**Foreign key:**

```sql
ALTER TABLE orders ADD CONSTRAINT orders_customer_fk
  FOREIGN KEY (customer_id) REFERENCES customers (id) NOT VALID;
ALTER TABLE orders VALIDATE CONSTRAINT orders_customer_fk;
```

The `ADD` takes `SHARE ROW EXCLUSIVE` on both tables (not `ACCESS EXCLUSIVE`), and the `VALIDATE`
takes `SHARE UPDATE EXCLUSIVE`. Because it never takes `ACCESS EXCLUSIVE`, existing readers and
writers are not blocked by the statement itself (writers still serialize against the constraint
check).

**Unique constraint:**

```sql
CREATE UNIQUE INDEX CONCURRENTLY users_email_key ON users (email);
ALTER TABLE users ADD CONSTRAINT users_email_key UNIQUE USING INDEX users_email_key;
```

The docs describe this exactly: create the unique index concurrently, then attach it as a constraint.
Caveat from the docs: if the index columns are not `NOT NULL`, `ADD PRIMARY KEY ... USING INDEX`
attempts `SET NOT NULL`, which requires a full scan to verify no nulls.

## 6. Type conversion with `USING`

- If there is no implicit or assignment cast, a `USING` clause is mandatory.
- The `USING` expression is **not** applied to the column default. `ALTER COLUMN ... SET DATA TYPE`
  can therefore fail on the default even when `USING` is given. The docs' remedy: `DROP DEFAULT`,
  alter the type, then `SET DEFAULT` with a compatible expression.
- **Diagnose the cast correctly before quoting an error.** If no implicit or assignment cast exists
  (e.g. `text` → `integer`), the statement fails at parse/plan time with
  `column "x" cannot be cast automatically to type <t>` and tells you to add `USING`. If an assignment
  cast *does* exist (`bigint` → `integer`), PostgreSQL attempts it per row and fails at runtime with
  `integer out of range` (SQLSTATE 22003) the moment a value does not fit. The whole statement aborts
  atomically. Do not report the wrong failure mode; it changes the fix.
- Type conversions that narrow the range (`bigint` → `integer`, `text` → `varchar(n)`,
  `numeric` → `integer`) either fail on out-of-range data or silently truncate, depending on the
  conversion. Silent truncation (`varchar(n)` with explicit truncation, `numeric` rounding) is the
  data-loss subset. Report the verification query (`SELECT max(x) …`), not just the risk.
- Statistics are removed after a type change; `ANALYZE` should follow.
- `ALTER COLUMN TYPE` on a column with a non-`NULL` default may also fail when the default expression
  cannot be cast; check the `DEFAULT` in the schema before approving.

## 7. `DROP COLUMN` specifics

- Does not reclaim disk space (docs: "will not immediately reduce the on-disk size of your table").
  Space is reclaimed as rows are updated, or after a rewrite (`VACUUM FULL`, `CLUSTER`).
- Requires `CASCADE` if anything outside the table depends on it: **views, foreign keys from other
  tables, generated columns, or a trigger function**. Without `CASCADE`, the statement fails; with
  it, dependent objects are dropped silently. Either way, the dependency set must be enumerated in
  the report. `CASCADE` is a destructive amplifier: list every object it will remove.
- Dropping a column also drops indexes and constraints that involve it.

## 8. Long-running transactions

- A rewrite or an `ALTER` that needs `ACCESS EXCLUSIVE` waits for every transaction that has touched
  the table. While it waits, it blocks all new readers and writers. Two mitigations must be in the
  report: (a) set a short `lock_timeout` on the migration session and retry, (b) check
  `pg_stat_activity` / `pg_locks` for long transactions before running.
- Idle-in-transaction sessions (`state = 'idle in transaction'`) are the most common cause. The
  check query belongs in the pre-deploy checklist.
- On a **logical replica / read replica**, DDL replay can be delayed behind long-running queries.

## 9. Renames

`ALTER TABLE ... RENAME COLUMN` takes `ACCESS EXCLUSIVE` (it is a rename among the listed subforms),
is instantaneous, and **breaks every query that uses the old name**. During a rolling deploy, version
N still queries the old name. Renaming is therefore an application-compatibility problem, not a lock
problem: it is `HIGH` when old application versions can still run, `BLOCKER` when the old version is
known to still be serving traffic. The safe pattern is add-new-column + dual-write + backfill +
switch + drop-old (see `safe-migration-patterns.md`).

## 10. Additional gotchas

- `ALTER TABLE ... ADD COLUMN ... NOT NULL DEFAULT x` on PG 11+ is a metadata-only change (non-volatile
  default). On PG 10 and older it rewrites the table. Version matters; do not report it as cheap
  without the version.
- `ALTER TABLE` can combine subcommands in a single statement, which takes the strictest lock needed
  and makes **one** pass over the table. Splitting them into several statements multiplies lock
  acquisitions and can increase risk.
- `ADD COLUMN IF NOT EXISTS` and `DROP COLUMN IF EXISTS` hide drift between the expected schema and
  the real one. They are not mitigations.
- Setting `lock_timeout` in the same transaction as other DDL is fine, but `SET lock_timeout` must be
  in effect **before** the `ALTER` starts waiting; a framework that runs `SET` after acquiring locks
  gives you no protection.
- `CONCURRENTLY` within an extension or a framework transaction wrapper is the single most common
  broken migration in PostgreSQL projects. Always state where the transaction boundary is.
- Deleting rows in bulk to "clean up" before a constraint is a backfill: it needs batching, and
  `DELETE` takes row locks and bloats the table.
