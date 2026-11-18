# Compatibility

Which databases and agents Reliant supports in Phase 1, which versions, and where each claim comes
from.

## 1. Database support

| Engine | Status | Versions covered | Notes |
|---|---|---|---|
| PostgreSQL | Supported | **Floor: 11.** Fixtures written against 14–16 | Closer to 10, the guarantees below do not hold; see the per-feature table |
| Oracle MySQL | Supported | **8.0 strongly; 5.7 only** as a "no INSTANT" comparison | InnoDB assumed; MyISAM is not modelled |
| MariaDB | **Not supported** | — | A fork with different online-DDL behaviour, `information_schema.metadata_lock_info` instead of `performance_schema.metadata_locks`, no atomic DDL, and a different default collation. MySQL rules do **not** transfer. Detection: `SELECT VERSION()` contains `MariaDB` |
| SQL Server, SQLite, Oracle | Not supported | — | Out of Phase 1 scope |

**When the engine or version is unknown** the skill must not claim an engine-specific path. It reports
the unknown, applies no MariaDB/MySQL-specific rule, and caps the verdict at `CONDITIONAL`. This is a
deliberate outcome, not a gap: an analysis with an unresolved engine is not evidence-based.

Docker images used to verify behaviour in CI: `postgres:16`, `mysql:8.0`. Both are pinned by major
version in `.github/workflows/validate-skills.yml`.

## 2. Version-sensitive PostgreSQL claims

Every row was checked against <https://www.postgresql.org/docs/current/sql-altertable.html> and the
linked pages. "Version" is the first version where the claim is true.

| Claim | Version | Where it is used |
|---|---|---|
| `ADD COLUMN` with a non-volatile default does not rewrite the table | 11+ | `references/postgres.md`, `NEG-004` |
| `ADD COLUMN … NOT NULL DEFAULT x` rewrites the table | ≤ 10 | Same; a report on PG 10 must not use the fast path |
| `CREATE INDEX CONCURRENTLY` outside a transaction | 8.2+ | `references/postgres.md`, `PG-006` |
| `DROP INDEX CONCURRENTLY` | 9.2+ | `references/safe-migration-patterns.md` |
| `REINDEX … CONCURRENTLY` | 12+ | `references/postgres.md` |
| `ADD CONSTRAINT … NOT NULL … NOT VALID` | 18 | `references/postgres.md`; older versions use the `CHECK NOT VALID` path |
| `ADD CONSTRAINT … FOREIGN KEY … NOT VALID` + `VALIDATE CONSTRAINT` | 9.1 / 9.1+ | `PG-004` |

## 3. Version-sensitive MySQL claims

Checked against the MySQL 8.0 Reference Manual (retrieved 2026-11-17) and the 8.0.12, 8.0.28, 8.0.29
release notes.

| Claim | Version | Where it is used |
|---|---|---|
| `ALGORITHM=INSTANT` exists | 8.0.12+ | `references/mysql.md` |
| INSTANT `ADD COLUMN` only at the end of the table | 8.0.12 – 8.0.28 | `MY-006`, `MY-007` |
| INSTANT `ADD COLUMN` at any position, INSTANT `DROP COLUMN` | 8.0.29+ | `references/mysql.md` |
| `RENAME COLUMN` syntax | 8.0.28+ | `MY-008` |
| `CHECK` constraints enforced | 8.0.16+ | `references/mysql.md` |
| Atomic DDL (single statement) | 8.0+ | `references/rollback-strategy.md` |
| `ADD FOREIGN KEY` with `ALGORITHM=INPLACE` | Only when `foreign_key_checks=0`; otherwise `COPY` | `MY-004` |
| Instant row-version limit (further instant add/drop is rejected) | 64 versions | `references/mysql.md` |
| `performance_schema.metadata_locks` | 8.0+ | `MY-006` mitigation |
| INSTANT accepts only `LOCK=DEFAULT` | 8.0+ | `MY-006`, `MY-007` |

### Claims deliberately not asserted

The manual does not state these, so Reliant does not claim them:

- A specific required lock level for building a `UNIQUE` index.
- That queries queue behind a pending `ALTER`'s metadata-lock request (the manual describes MDL
  contention and lock priority, not a documented FIFO queue). Reported as a queueing risk with
  `metadata_locks.LOCK_STATUS='PENDING'` as the observable.
- The behaviour of `ADD COLUMN … NOT NULL` without a default on a non-empty table under INSTANT.
- That a table is implicitly rebuilt when instant row versions accumulate; the documented behaviour is
  rejection at the limit with a recommendation to rebuild explicitly.

If a future release documents any of these, add the row with its source.

## 4. Agent / client support

The Agent Skills format is an open specification. Reliant ships the skill in one place and exposes it
through compat layers that different agents discover by default.

| Client | Discovery path | How Reliant exposes it |
|---|---|---|
| Claude Code | `.claude/skills/<name>/SKILL.md` | `.claude/skills/migration-guard` points at `skills/migration-guard` |
| VS Code / GitHub Copilot | `.agents/skills/<name>/SKILL.md` | `.agents/skills/migration-guard` points at the same directory |
| Any other spec-compliant agent | implementation-defined | Point it at `skills/migration-guard` directly |

The compat layers are links, not copies. `scripts/validate_repo.py` fails if a compat entry resolves to
a different `SKILL.md` than the source. On Windows checkouts without symlink support, each compat entry
contains a `symlink.txt` naming the target; treat that as a pointer for manual setup, not as a working
skill.

### Format conformance

- `name`: required, ≤64 chars, lowercase alphanumeric and single hyphens, equals the folder name.
- `description`: required, ≤1024 chars, states what the skill does and when to use it.
- `compatibility`: ≤500 chars.
- `metadata`: map of string values.
- `SKILL.md`: kept under 500 lines / ~5000 tokens; detailed material lives in `references/` and is
  loaded on demand.

Validated locally by `scripts/validate_repo.py` and, as a secondary check, by the pinned `skills-ref`
validator in CI.

## 5. Host requirements

| Requirement | Detail |
|---|---|
| File read access | The skill reads the repository. No other access is needed |
| Network | Not required for analysis. Required only for `skills-ref` during CI |
| Database | Not required. Reliant never connects to a database |
| Python (CI only) | 3.9+ standard library |

## 6. Known gaps

- Lock durations and row counts are not measured; they are derived from fixtures or user input. The
  skill lists them as unknowns when absent.
- Mobile clients, BI dashboards, and external consumers are only covered if the repository documents
  them. `application-usage.md` in each fixture models that reality, including a "not searched" section.
- MySQL behaviour under `foreign_key_checks=0`, `pt-online-schema-change`, or `gh-ost` is not modelled.