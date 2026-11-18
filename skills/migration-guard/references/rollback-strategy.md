# Rollback Strategy

Load this file when the migration is destructive, when the user asks about rollback, or when you need
to fill the report's "Rollback and recovery" section.

Three things are routinely called "rollback" and are not interchangeable. Evaluating one does not
evaluate the others.

Documentation for the engine behaviour cited here lives in `postgres.md` and `mysql.md`. Relevant
official sources:

- PostgreSQL `ALTER TABLE` (DROP COLUMN does not reclaim space; rewrites are not MVCC-safe):
  <https://www.postgresql.org/docs/current/sql-altertable.html>
- PostgreSQL backup and PITR: <https://www.postgresql.org/docs/current/continuous-archiving.html>
- MySQL statements that cause an implicit commit (DDL cannot be rolled back):
  <https://dev.mysql.com/doc/refman/8.0/en/implicit-commit.html>
- MySQL atomic DDL (a single DDL statement is crash-safe; it is not transactional DDL):
  <https://dev.mysql.com/doc/refman/8.0/en/atomic-ddl.html>
- MySQL backup and recovery: <https://dev.mysql.com/doc/refman/8.0/en/backup-and-recovery.html>

| Name | What it restores | What it does **not** restore |
|---|---|---|
| **Schema rollback** | The structure: columns, types, constraints, indexes | Any rows or values that were deleted or overwritten |
| **Data recovery** | Rows and values that were lost or changed | Structure added after the backup point; work done since |
| **Application rollback** | The previous running code version | Schema changes already applied; data already migrated |

## 1. Schema rollback

Restoring the shape. Reversibility depends on the operation:

| Operation | Reverse statement | Truly restores? |
|---|---|---|
| `ADD COLUMN` | `DROP COLUMN` | Yes, if nothing was written to the column |
| `DROP COLUMN` | `ADD COLUMN <same type>` | **No** — the values are gone |
| `ALTER COLUMN TYPE` (narrowing) | `ALTER COLUMN TYPE <wider>` | **No** — truncated values cannot come back |
| `ADD CONSTRAINT` | `DROP CONSTRAINT` | Yes |
| `DROP CONSTRAINT` | `ADD CONSTRAINT` | Only if the data still satisfies it |
| `CREATE INDEX` | `DROP INDEX` | Yes |
| `RENAME` | Rename back | Yes, if code is not still reading either name |
| `CREATE TABLE` | `DROP TABLE` | Only if empty |
| `DROP TABLE` | Recreate table definition | **No** — rows are gone |

**Down migrations are the most dangerous code in the repository.** A `down` that recreates a dropped
column makes the schema look restored while all values are `NULL` or defaulted. Prefer stating "not
reversible; restore from backup" over writing a down migration that silently fakes a rollback.

Report wording that must appear for irreversible schema steps:

> Schema rollback restores the column but not its values. Recovering the values requires the
> pre-migration backup/restore (see Data recovery). The down migration on its own is not a rollback
> of this change.

## 2. Data recovery

The only real protection against a destructive change. State, for each destructive step:

- **What exactly is lost.** Column values? Rows? Which rows (all, or a subset selected by a predicate)?
- **How it can be recovered.** Which of these exists: physical backup (PITR, snapshot), logical dump,
  an archival copy written before the drop, or reproducibility (derived/recomputable data)?
- **RPO — how much recent data is lost.** "Restore to the most recent nightly backup" means up to 24
  hours of writes are gone, **including unrelated tables changed after that point**.
- **RTO — how long recovery takes.** Restoring a large database is not instantaneous; say so.
- **The blast radius of restoring.** A full restore reverts every table to the backup point, not only
  the one you broke. Confirm whether a logical/partial recovery is possible.

Rules:

1. **Snapshot/export before the destructive step is the gate.** If the migration drops or rewrites
   data and no snapshot or copy is confirmed, that is a `BLOCKER` regardless of everything else.
2. A backup that has never been **restore-tested** is a hypothesis. Say "backup exists; restore has
   not been verified" rather than "data is recoverable".
3. For a column drop on PostgreSQL, the values *may* still be on disk in old row versions until
   vacuumed, but do **not** present reading them back as a recovery plan. It is unreliable and
   unsupported.
4. On MySQL, `DROP COLUMN`/`DROP TABLE` is immediate; there is no PostgreSQL-style invisible column.
5. **DDL is not undone by a transaction on either engine.** PostgreSQL rolls back DDL because it is
   transactional (except `CONCURRENTLY` forms), while MySQL commits DDL immediately. A migration file
   that "looks atomic" is not atomic on MySQL; partial application is a real failure mode, and the
   recovery is the backup, not a `ROLLBACK`.

## 3. Application rollback

Reverting the code does not revert the schema. The real question is the **compatibility matrix**:

| Schema state | Can version N-1 run? |
|---|---|
| Pure additive (new nullable columns, new tables, new indexes) | Yes |
| Column renamed / dropped | **No** — old code references the old name |
| Column added as `NOT NULL` without default | **No** — old code inserts without the column |
| Column type narrowed / changed | Depends — old code may fail to read or write |
| New `NOT NULL`/`UNIQUE`/`FK` constraint enforced | Depends — old code may violate it |
| Data already backfilled with the new semantics | **No** — reverting code against new data can produce wrong results |

**Deploy-order rule:** during a rolling deploy both versions run against the same schema. The schema
must satisfy version N-1 **and** N. That is the definition of a backward-compatible migration. If the
migration is not backward compatible, the deploy must stop the world, which must be approved.

**Rollback-order rule:** you roll back code first and schema second, and only if the schema change is
backward compatible. Rolling back the schema first breaks the code that is still running.

## 4. What every report must separate

Fill the report's "Rollback and recovery" section with three separate lines. Never collapse them:

```markdown
**Schema rollback:** Reversible — `down` drops orders.status. / Irreversible — dropping
`users.phone` destroys its values.

**Data recovery:** Pre-migration snapshot required. Restore point: <backup>. RPO: <if stated>.
Without it the drop is a BLOCKER.

**Application rollback:** Safe only after the application stops reading `phone`. Rolling back to
version N-1 while the column is gone causes `UndefinedColumn` on the user profile endpoint.
```

## 5. Recovery-plan checklist for destructive changes

Apply this checklist only to **irreversible** steps. A reversible backfill (filling a `NULL` column,
recomputing derived data) does not need a restore point; it needs an idempotent predicate and commit
per batch, which `safe-migration-patterns.md` covers.

For an irreversible step:

- [ ] Backup/snapshot identified, with type (physical PITR, logical dump, snapshot)
- [ ] Restore path written down and tested at least once
- [ ] RPO and RTO stated
- [ ] Data that is lost is named precisely
- [ ] Down migration either correctly restores data or is explicitly absent
- [ ] Application rollback compatibility assessed for version N-1
- [ ] Human approval to accept risk recorded, with the person's role

If any of the first three is missing, the finding is `BLOCKER` — not because the step is destructive,
but because the loss is unrecoverable.
