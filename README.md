# Reliant

**Engineering Skills for Safer Software Changes**

> Prove your changes are safe.

Reliant is a collection of Agent Skills that help coding agents analyse the risk of a change — database
schema, data, and application compatibility — *before* the change is considered done. It is not a
framework, not a CLI, and not an application you run. It has no runtime and needs no database
connection. Reliant reads the repository, reasons about evidence, and refuses to approve what it cannot
substantiate.

The central rule: **a valid migration is not a safe migration.** `ALTER TABLE users DROP COLUMN phone;`
is valid SQL and breaks every code path that still selects `phone`.

## Status

| | |
|---|---|
| Version | v0.1.0 — unreleased, **release blocked** |
| Phase | 1 — Foundation and Migration Guard |
| Engines | PostgreSQL, MySQL |
| Skills | 1 (`migration-guard`) |
| Evaluation | Runs recorded; **below target** — see [`docs/evaluation.md`](docs/evaluation.md) |

## Evaluation status

The skill has been evaluated and **does not meet its release criteria**. Two iterations on
2026-11-18 measured required-finding recall far below its target and **zero format compliance**: every
report used a different structure than the mandated contract. The analysis was often correct; the
report format was not.

Those runs are self-evaluated — the fixtures were written by the same model family being tested — so
they measure internal consistency, not independent quality. The numbers, the causes, and the required
remediation are in [`docs/evaluation.md`](docs/evaluation.md) §7. Until format compliance is fixed and
re-measured, v0.1.0 is not released.

## What Migration Guard does

Given a migration and the repository it belongs to, the skill:

1. Identifies the engine and version, and the current schema.
2. Parses every statement in the migration.
3. Traces application usage for each affected table, column, index, and constraint.
4. Assesses lock, rewrite, backfill, and deploy-order impact.
5. Produces an evidence-backed report with a risk level per finding and a verdict.

Risk levels: `BLOCKER`, `HIGH`, `MEDIUM`, `LOW`, `INFO`.
Verdicts: `BLOCKED`, `CONDITIONAL`, `APPROVED`.

`APPROVED` means *no blocker found in the inspected scope with the available evidence* — it is not a
production guarantee, and every approved report says so.

## Install

Reliant is a set of files. There is nothing to build.

| Client | Path to use |
|---|---|
| VS Code / GitHub Copilot | `.agents/skills/migration-guard` (already in this repo) |
| Claude Code | `.claude/skills/migration-guard` (already in this repo) |
| Any other spec-compliant agent | point it at `skills/migration-guard` |

Both compat paths are links to the single source of truth in `skills/migration-guard`. On a Windows
checkout without symlink support they contain a `symlink.txt` pointer; see that file for the one-line
command to create a working link.

You can also copy `skills/migration-guard/` into your own project's skill directory.

## Use

Ask the agent to use the skill when reviewing a migration:

```text
Use the migration-guard skill to review db/migrate/20261116_add_index.sql.
Is this safe to deploy?
```

The skill prompts the agent to read the schema and the application code before answering, and to state
what it could not inspect.

## Repository layout

```text
skills/migration-guard/
├── SKILL.md                  instructions the agent loads
├── references/               technical detail, loaded on demand
│   ├── risk-classification.md
│   ├── postgres.md
│   ├── mysql.md
│   ├── safe-migration-patterns.md
│   └── rollback-strategy.md
└── examples/                 a correct APPROVED report and a correct BLOCKED report
tests/migration-guard/        20 cases, expected results, harness prompt
docs/                         design, evaluation, compatibility, security model
scripts/                      repository validator, report scorer
```

## Evaluation

The suite has 8 PostgreSQL cases, 8 MySQL cases, and 4 negative cases. Scoring:

```bash
python scripts/evaluate_reports.py --reports evals/results/iteration-1
```

The metrics and their targets are defined in [`docs/evaluation.md`](docs/evaluation.md). They are
targets, not results: no run has been recorded yet.

## Validate the repository

```bash
python scripts/validate_repo.py
```

Checks Agent Skills frontmatter, required files, test fixture completeness, expected-result consistency,
and that the compat layers resolve to the same skill.

## Principles

1. Evidence before assumptions — a finding without a citation is an unknown, not a finding.
2. Inspect before modifying — know what exists and who uses it before recommending a change.
3. Safe changes over quick changes — an instant `RENAME` can be a slower *change* than a four-step
   expand-and-contract.
4. Verification over confidence — every recommendation ships with a way to check it.
5. Human approval for destructive changes — an agent cannot approve its own data loss.

See `docs/design-principles.md` for the reasoning, and `docs/security-model.md` for what the skill is
allowed to do (and why an Agent Skill is not a sandbox).

## Scope

**In scope (Phase 1):** database migration safety for PostgreSQL and MySQL.

**Out of scope:** API contract checks, authorization checks, behaviour preservation, release checks,
MariaDB/SQL Server/SQLite, and executing migrations. These are later phases, not omissions.

## License

MIT. See `LICENSE`.