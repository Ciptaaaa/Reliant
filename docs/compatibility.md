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
| **Kilo Code** | `skills.paths` in `kilo.json`; also `.kilo/skills/` and `.agents/skills/` | `kilo.json` declares `"skills": {"paths": ["./skills"]}` — no copy, no link |
| Claude Code | `.claude/skills/<name>/SKILL.md` | needs a link; see below |
| VS Code / GitHub Copilot | `.agents/skills/<name>/SKILL.md` | needs a link; see below |
| Any other spec-compliant agent | implementation-defined | point it at `skills/migration-guard` |

### Kilo Code: the `kilo.json` path is the supported one

Kilo Code reads skills from `skills.paths` in `kilo.json`, from `.kilo/skills/`, and from
`.agents/skills/` (the open standard directory, loaded by default). The repository uses the first:

```json
{
  "skills": { "paths": ["./skills"] }
}
```

This is the only option that works **and** can be committed without duplication:

- A copy into `.kilo/skills/` works, but Git then stores the whole skill twice and the copies drift.
- A junction into `.agents/skills/` or `.kilo/skills/` works locally, but Git follows a junction on
  Windows and commits every file inside it — the same duplication, silently.
- `.agents/skills/migration-guard/SKILL.md` does not exist in a fresh checkout, because the repository
  has `core.symlinks=false` and the compat entry is only a `symlink.txt` pointer.

**Skills are discovered at session start.** After adding or changing `kilo.json`, run `/reload` in a
new session; an already-open session will not see the skill.

Kilo enforces one extra rule the base spec also states: the frontmatter `name` must match the parent
directory name. `skills/migration-guard/SKILL.md` declares `name: migration-guard`, which matches.

### The compat layer is inactive as committed

Checked on a fresh clone: `.claude/skills/migration-guard/SKILL.md` and
`.agents/skills/migration-guard/SKILL.md` do not exist, so an agent that only looks in those paths finds
nothing. This is not a bug in the checkout, and the repository does not work around it: a committed link
under `core.symlinks=false` is stored as a plain text file that no agent reads, and a committed copy
would drift. The pointer file is a note, not a fallback. Until the command below is run, the only client
that finds the skill is Kilo Code through `kilo.json`.

`scripts/validate_repo.py` warns while the pointer file is in place. Treat that warning as a setup item
list: on a machine where you use Claude Code or Copilot, a clean validator run means the link exists;
on a machine that only uses Kilo Code, it is expected and can be ignored.

To activate one, remove the pointer directory first — every link command refuses an existing target —
then create the link from the repository root:

```bash
# macOS / Linux
rm -rf .agents/skills/migration-guard
ln -s ../../skills/migration-guard .agents/skills/migration-guard

# Windows (cmd) — a junction needs no Developer Mode
rmdir /S /Q .agents\skills\migration-guard
mklink /J .agents\skills\migration-guard skills\migration-guard
```

`.claude/skills/migration-guard` is at the same depth, so the same `../../` target applies.

Verify: `.agents/skills/migration-guard/SKILL.md` exists. Do **not** replace the pointer with a copied
directory: git stores the skill twice and the copies drift. That was tried during release verification
and reverted for exactly that reason.

### What a release has to decide

The compat layer is a per-machine setup step, not a distributable artifact, and v0.1.0 ships it that way.
The release notes must say so in one line, because "skills are auto-discovered" is false for every client
except Kilo Code until the link command runs.

### Format conformance

- `name`: required, ≤64 chars, lowercase alphanumeric and single hyphens, equals the folder name.
- `description`: required, ≤1024 chars, states what the skill does and when to use it.
- `compatibility`: ≤500 chars.
- `metadata`: map of string values.
- `SKILL.md`: kept under 500 lines / ~5000 tokens; detailed material lives in `references/` and is
  loaded on demand.

Validated locally by `scripts/validate_repo.py` and by the `skills-ref` validator pinned to a commit SHA
in CI. The `skills-ref` job is a gate: a failure fails the workflow, and a pin that cannot be fetched
also fails rather than silently skipping the check.

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