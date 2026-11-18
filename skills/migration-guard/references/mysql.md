# MySQL Migration Safety

Load this file when the migration targets **Oracle MySQL**. It does not cover MariaDB.

> **Scope warning.** This file describes Oracle MySQL 8.0 (and notes the MySQL 5.7 differences that
> matter). MariaDB is a fork with different online-DDL support, different system tables, and different
> defaults. Do not apply these rules to MariaDB. If the engine is MariaDB, say the analysis is out of
> scope and cap the verdict at `CONDITIONAL`.

MySQL's online DDL support is **highly version- and operation-specific**. Never generalise "it is
online" across operations.

Primary sources (MySQL 8.0 Reference Manual, retrieved 2026-11-17):

- Online DDL operations: <https://dev.mysql.com/doc/refman/8.0/en/innodb-online-ddl-operations.html>
- Online DDL limitations: <https://dev.mysql.com/doc/refman/8.0/en/innodb-online-ddl-limitations.html>
- `ALTER TABLE`: <https://dev.mysql.com/doc/refman/8.0/en/alter-table.html>
- Atomic DDL: <https://dev.mysql.com/doc/refman/8.0/en/atomic-ddl.html>
- Statements that cause an implicit commit: <https://dev.mysql.com/doc/refman/8.0/en/implicit-commit.html>
- Metadata locking: <https://dev.mysql.com/doc/refman/8.0/en/metadata-locking.html>
- `performance_schema.metadata_locks`:
  <https://dev.mysql.com/doc/refman/8.0/en/performance-schema-metadata-locks-table.html>
- InnoDB limits (index prefix length): <https://dev.mysql.com/doc/refman/8.0/en/innodb-limits.html>

## 1. `ALGORITHM` — what each value guarantees

| Value | Meaning |
|---|---|
| `INSTANT` | Metadata-only operation; no table rebuild, no data touched |
| `INPLACE` | Operates in place; may still rebuild the table and may still block DML |
| `COPY` | Copies the table into a new one; always blocks concurrent DML |
| *(omitted)* | Server tries INSTANT, then INPLACE, then COPY, in that order |

Two rules that matter for every review:

1. **Specifying an algorithm is a promise the server enforces.** If `ALGORITHM=X` is named and the
   operation cannot use `X`, MySQL **fails the statement immediately and changes nothing**. Naming the
   algorithm is therefore the *safe* form: it converts a silent blocking copy into a visible error.
   Recommend it in reports.
2. **Omitting the algorithm is how outages happen.** A statement with no algorithm may silently fall
   back to `COPY`, which blocks all DML for the whole copy. If the migration has no explicit
   clause and the operation is not known to be INSTANT/INPLACE, the report must say the path is
   unknown.

`ALGORITHM=INSTANT` only accepts `LOCK=DEFAULT`. `COPY` operations always include at least
`LOCK=SHARED` restrictions.

## 2. `INSTANT` support by operation (8.0)

`INSTANT` was introduced in **8.0.12** and became the default algorithm for the operations that support
it. MySQL **5.7 has no `INSTANT` at all** — the online-DDL tables in the 5.7 manual have no Instant
column, and adding a column in 5.7 rebuilds the table. Do not describe an INSTANT path for a 5.7
target.

Supported with `ALGORITHM=INSTANT` on 8.0.29+:

| Operation | Notes |
|---|---|
| `ADD COLUMN` | 8.0.12: only as the **last** column. 8.0.29+: any position. |
| `DROP COLUMN` | 8.0.29+ only. Before that, `DROP COLUMN` is INPLACE with a rebuild. |
| `RENAME COLUMN` | `RENAME COLUMN` syntax arrived in 8.0.28; rename is INSTANT. |
| `SET/DROP DEFAULT` | Instant |
| Append to `ENUM`/`SET` | Instant when the storage size does not change |
| Rename table / change index type / add-drop **virtual** generated column | Instant |

**Not** INSTANT: changing a column's data type, adding/dropping a stored generated column, adding or
dropping a foreign key, converting a charset.

### The 64-row-version limit

Each instant `ADD`/`DROP COLUMN` increments `TOTAL_ROW_VERSIONS` for the table
(`INFORMATION_SCHEMA.INNODB_TABLES`). After **64** instant row versions, further instant add/drop is
**rejected** (error 4092) and the table must be rebuilt with an explicit `ALGORITHM=COPY` or `INPLACE`.
A long series of instant column changes therefore ends in a forced rebuild — worth flagging for teams
that add columns frequently. Also watch the 1022-column limit and row-size errors.

## 3. `INPLACE` support and lock levels

| Operation | In place | Rebuilds table | Concurrent DML |
|---|---|---|---|
| Add secondary index (incl. unique) | Yes | No | Yes |
| Drop secondary index | Yes | No | Yes |
| Add `FULLTEXT` index | Yes | Only if there is no user-defined `FTS_DOC_ID` | **No** |
| Drop foreign key | Yes | No | Yes |
| Add foreign key | **Only with `foreign_key_checks=0`** — otherwise `COPY` only | — | Yes when in place |
| Change column data type | **No** | Yes | **No** — `ALGORITHM=COPY` |
| Convert character set | Yes | Yes | **No** |
| Add/drop `CHECK` constraint | Yes | No | Yes |
| Rebuild / `FORCE` | Yes | Yes | Yes |

`LOCK` levels: `NONE` (reads and writes allowed, else error), `SHARED` (reads allowed, writes blocked),
`EXCLUSIVE` (all DML blocked), `DEFAULT` (maximum concurrency the operation permits).

**Adding a unique index**: the manual states that a secondary index — including unique — supports
concurrent DML. It does **not** document that unique index creation requires `LOCK=SHARED`. Do not
assert a lock level the documentation does not state. Instead, say the safest reviewable form is to
name the algorithm and let the server report the lock it takes (and verify on staging).

**Adding a foreign key** is the most common trap: it is *not* generally an online operation. With
`foreign_key_checks` enabled (the default), MySQL uses `COPY`, which blocks DML for the whole copy. Any
report that promises `ALGORITHM=INPLACE, LOCK=NONE` for adding an FK without mentioning this condition
is wrong.

## 4. Metadata locks and the pile-up

Every DDL statement takes a metadata lock (MDL) on the table. The manual states that a table used by a
transaction cannot be used in DDL by other sessions until that transaction ends, and that an online DDL
operation must wait for transactions holding metadata locks — a long-running or idle transaction makes
it time out.

The consequential behaviour that reviewers must plan for: while the `ALTER` waits for its MDL, new
queries on the same table request the same MDL, and the server serialises these requests — so a
blocked `ALTER` can stall traffic on a busy table for as long as it waits. The manual does not spell
this out as a sentence, so reports should phrase it as a queueing risk and point at the observable:
`performance_schema.metadata_locks` columns `LOCK_STATUS` (`PENDING`, `GRANTED`, `TIMEOUT`, `KILLED`)
and `sys.schema_table_lock_waits`.

Mitigations to put in the report:

- Set `lock_wait_timeout` for the migration session (e.g. `SET SESSION lock_wait_timeout = 5;`) so a
  busy moment fails fast instead of stalling the table.
- Run the change outside known long-transaction windows.
- Prefer `ALGORITHM=INSTANT` where supported, so the MDL is held for a fraction of a second.

## 5. DDL is not transactional

- DDL statements cause an **implicit commit**; they cannot be run inside a transaction and cannot be
  rolled back. Consequently a migration file containing **several** DDL statements can apply partially:
  statement 1 commits, statement 2 fails, and you are left in a half-changed schema.
- **Atomic DDL** (8.0) makes a **single** DDL statement atomic and crash-safe together with its data
  dictionary and binlog changes. It is explicitly "not transactional DDL" and does **not** group
  multiple statements.
- Review rule: a multi-statement DDL migration on MySQL is always at least `MEDIUM` (partial-failure
  mode), and needs a documented resume/cleanup step for each intermediate state.

## 6. Constraints: data must be clean *before* the DDL

MySQL has **no `NOT VALID` equivalent** for `FOREIGN KEY` or `CHECK`. `CHECK` accepts only `ENFORCED`
(default) or `NOT ENFORCED`, and `NOT ENFORCED` is not deferred validation — the constraint is simply
not checked.

Therefore, for MySQL, the constraint is validated at creation time and fails on bad data:

| Attempt | Failure |
|---|---|
| `MODIFY … NOT NULL` with `NULL` rows | `ERROR 1138: Invalid use of NULL value` |
| `UNIQUE` index with duplicates | `ERROR 1062: Duplicate entry … for key` |
| `ADD FOREIGN KEY` with orphans | `ERROR 1452: Cannot add or update a child row` |
| Delete a referenced parent (default action) | `ERROR 1451: Cannot delete or update a parent row` |

The safe pattern is always: **prove the data first** (detection query), **fix it in batches**, **change
the writers so they no longer produce bad rows**, then run the DDL. Detection queries belong in the
report, and the number of offending rows must be quoted from the provided evidence.

`ON DELETE`/`ON UPDATE` default to `RESTRICT`-like behaviour. Adding an FK therefore changes the
behaviour of existing delete paths; enumerate the application's delete code before approving.

## 7. Charset and collation

- MySQL 8.0 defaults: `utf8mb4` / `utf8mb4_0900_ai_ci`. `ai_ci` is **accent- and case-insensitive**,
  so `'ABC'` and `'abc'` collide for a `UNIQUE` index. When reviewing a unique index, check the
  collation: the effective duplicate count is the **case-insensitive** count, not the exact-match count.
- 5.7 server default is `latin1` (many distributions override it) — do not assume `utf8`.
- `CONVERT TO CHARACTER SET` is an in-place **rebuild with no concurrent DML**: it blocks writes for the
  whole conversion.
- Index prefix limits still apply in 8.0: 3072 bytes for `DYNAMIC`/`COMPRESSED` row format, 767 bytes for
  `REDUNDANT`/`COMPACT`. With `utf8mb4` that is ~191 characters per prefix. The 5.7-only
  `innodb_large_prefix` switch is removed; `DYNAMIC` is the default.

## 8. Version compatibility summary

| Feature | Minimum version |
|---|---|
| `LOCK=NONE` / online DDL for secondary indexes | 5.6+ |
| `ALGORITHM=INSTANT` (add column, last position) | 8.0.12 |
| `CHECK` constraints enforced | 8.0.16 |
| `RENAME COLUMN` syntax | 8.0.28 |
| `INSTANT` drop column / add column at any position | 8.0.29 |
| Atomic DDL | 8.0 |

If the target version is unknown, the report must not claim an INSTANT path and must cap the verdict at
`CONDITIONAL` — the same operation is a rebuild on 5.7 and metadata-only on 8.0.29.

### MariaDB is out of scope

Everything above is sourced from the Oracle MySQL manual. MariaDB diverges in ways that matter here:

| Area | Oracle MySQL | MariaDB |
|---|---|---|
| Metadata lock inspection | `performance_schema.metadata_locks` | `information_schema.metadata_lock_info` |
| `ALGORITHM=INSTANT` | 8.0.12+ | Not available under that name; MariaDB uses `ALGORITHM=INSTANT` from 10.3 but with a smaller operation set |
| Atomic DDL | 8.0+ | No equivalent |
| `RENAME COLUMN` | 8.0.28+ | 10.5.2+ |
| Default collation | `utf8mb4_0900_ai_ci` | `utf8mb4_general_ci` |
| Version detection | `SELECT @@version` | `SELECT VERSION()` contains `MariaDB` |

The table is here to explain **why** the rules do not transfer, not to license applying them. Reliant
does not cover MariaDB in Phase 1.

**Detection:** `SELECT VERSION()` returns a string containing `MariaDB` on MariaDB. If the engine cannot
be identified as Oracle MySQL, the report must say so, must not claim INSTANT or a specific lock level,
and must cap the verdict at `CONDITIONAL`. Telling the user "if this is MariaDB, these findings do not
apply" is the correct outcome, not a failure.

## 9. Gotchas

- `SHOW WARNINGS` after an `ALTER` reports whether MySQL used a different algorithm than requested —
  use it on staging.
- A `RENAME COLUMN` is instant for the engine and still breaks every query using the old name. On MySQL
  the older `CHANGE COLUMN old new <full definition>` form also **re-specifies the type**: forgetting a
  `NOT NULL`, a charset, or a default silently changes the column. Flag `CHANGE COLUMN` rewrites that
  are not identical to the original definition.
- `ADD COLUMN` with `NOT NULL` **and no default** on a non-empty table is not documented for INSTANT
  backfilling; treat it as needing verification on the target version rather than asserting it works.
- `DROP INDEX` on a secondary index is INPLACE/online; it is not the same risk as PostgreSQL's blocking
  `DROP INDEX`.
- Replication: long DDL is replayed on replicas and causes lag. With reporting on a replica, lag can
  break read SLAs even when the primary was fine.
- Multi-statement DDL + implicit commit + no `NOT VALID` = the most common MySQL deploy failure. If the
  migration file has more than one statement that changes data-bearing structure, the report must call
  it out even when each statement is individually safe.