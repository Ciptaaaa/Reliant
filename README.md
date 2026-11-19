# Reliant

**Engineering Skills for Safer Software Changes**

> Prove your changes are safe.

Reliant is a collection of [Agent Skills](https://agentskills.io/home) that help coding agents judge the
risk of a change before it is considered done. It is not a framework, a CLI, or a service: it has no
runtime and needs no database connection. The agent reads the repository, and the skill forces every
claim to carry evidence.

The central rule: **a valid migration is not a safe migration.** `ALTER TABLE users DROP COLUMN phone;`
is valid SQL and breaks every code path that still selects `phone`.

## Status

| | |
|---|---|
| Version | v0.1.0 — **unreleased** |
| Phase | 1 of 6 — Migration Guard |
| Engines | PostgreSQL, MySQL |
| Skills | 1 (`migration-guard`) |
| Test suite | 20 cases (8 PostgreSQL, 8 MySQL, 4 negative) |
| Evaluation | One iteration meets every metric, at 1 run per case — not yet enough to release |

Phases 2–6 (API contract, authorization, behaviour preservation, release safety, ecosystem) are planned
and not started. Nothing outside Phase 1 is in this repository.

## What Migration Guard does

Given a migration and the repository it belongs to, the skill:

1. Identifies the engine, its version, and the current schema.
2. Parses every statement in the migration.
3. Traces application usage for each affected table, column, index, and constraint.
4. Separates three different questions: is the change **reversible**, does it **lose data**, and what
   does it cost **while it runs**.
5. Produces a report with a severity per finding, a verdict, and a `path:line` citation on every finding.

Severities: `BLOCKER`, `HIGH`, `MEDIUM`, `LOW`, `INFO`.
Verdicts: `BLOCKED`, `CONDITIONAL`, `APPROVED`.

`APPROVED` means *no blocker was found in the inspected scope with the evidence available*. It is not a
production guarantee, and every approved report says so.

### Why the three questions are separate

Collapsing them is what produces bad reports. A constraint that the engine **refuses** loses no data and
is fully reversible — it is still a `BLOCKER` because the deploy fails, but no human needs to accept data
loss for it. A `RENAME` moves values rather than deleting them, so it is reversible, yet it breaks old
application instances mid-deploy — that needs a *schedule*, not *accept-risk*. A `DROP COLUMN` with values
is the case that genuinely needs `accept-risk` and a restore point.

The definitions live in
[`references/change-impact-taxonomy.md`](skills/migration-guard/references/change-impact-taxonomy.md) and
are written to be reused by Phases 2–6.

## Evaluation

Twenty cases, scored by `scripts/evaluate_reports.py` against frozen expectations in
`tests/migration-guard/expected/`. No expectation was relaxed to improve a score; where a fixture was
found to contradict its own operation, the fixture was corrected and the change recorded in
[`CHANGELOG.md`](CHANGELOG.md) and [`docs/evaluation.md`](docs/evaluation.md) §7.

The most recent recorded iteration, `iteration-4`, meets every metric in the suite. Earlier iterations
did not: format compliance and evidence coverage were near zero, which is what drove the contract and
linter work in this release.

**The metric table is kept in one place, [`docs/evaluation.md`](docs/evaluation.md) §7**, together with
the scorecards, the per-iteration deltas, and the commands to regenerate them. It is not duplicated here,
so the two cannot drift apart.

Read those numbers with three caveats, all recorded in `docs/evaluation.md` §7 and in
[`evals/results/iteration-4/iteration-meta.json`](evals/results/iteration-4/iteration-meta.json):

- **Self-evaluated.** The reports in iteration-4 were written by the same agent that wrote the report
  contract and the linter. That shows the contract is *followable* by an agent with a template and a
  linter. It does **not** show that another model follows it. Iterations 1–2, run through a tool-less
  path, never followed the format once.
- **One run per case**, where the protocol asks for three. A second run of `PG-001` was written during
  the session and scored 2/3 findings instead of 3/3, so the single-run figure is not a stable estimate.
- **The columns differ in more than the skill.** Iterations 1–2 used a different runner (single-turn API,
  no tools) and older skill versions. This is not a controlled comparison.

An earlier iteration that also met every metric was destroyed before it was committed by a bug in the
evaluation tooling (`prepare_eval_workspaces.py` followed a `--cases ..` traversal into `rmtree`). The
bug is fixed and covered by a test. Those numbers are not quoted anywhere.

Reproduce the numbers:

```bash
python scripts/evaluate_reports.py --reports evals/results/iteration-4 --report-only --quiet-cases
```

### Why v0.1.0 is not released

Every metric passes, so the remaining blockers are about evidence, not code:

| Criterion | Status |
|---|---|
| Every metric at target on a recorded iteration | Met on iteration-4, but that run is self-evaluated and one run per case; it is not quoted as proof |
| Three runs per case | **Not met** — iteration-4 recorded one run per case |
| Run on a model family other than the one that wrote the suite | **Not met** — never attempted |

## Install

Reliant is a set of files. There is nothing to build.

| Client | How it finds the skill |
|---|---|
| **Kilo Code** | works as committed: `kilo.json` declares `"skills": {"paths": ["./skills"]}` |
| Claude Code, VS Code / Copilot, other agents | needs one link command, below |

Kilo Code reads `skills.paths` from `kilo.json`, so it needs no setup. Skills are discovered at session
start: after changing the config, run `/reload` or start a new session.

The other clients look in `.claude/skills/<name>/SKILL.md` and `.agents/skills/<name>/SKILL.md`. This
repository is committed with `core.symlinks=false`, so those paths contain a `symlink.txt` pointer rather
than a working link: **as committed, Claude Code and Copilot do not find the skill.** The pointer files
record the command and the reason; they are not a fallback that works on their own. For every client
other than Kilo Code, run the setup once after cloning.

Create the link from the repository root — the pointer directory must be removed first, because every
link command refuses an existing target:

```bash
# macOS / Linux
rm -rf .agents/skills/migration-guard
ln -s ../../skills/migration-guard .agents/skills/migration-guard

# Windows (cmd) — a junction needs no Developer Mode
rmdir /S /Q .agents\skills\migration-guard
mklink /J .agents\skills\migration-guard skills\migration-guard
```

Use `.claude/skills/migration-guard` instead for Claude Code, including the `../../` prefix in the `ln -s`
target because that directory sits at the same depth. Verify with
`test -f .agents/skills/migration-guard/SKILL.md`; `python scripts/validate_repo.py` warns while the
pointer file is still in place, so a clean run is the proof that the link exists. Do not copy the
directory in place of the link: git would store the skill twice and the copies would drift.

To use the skill in another project, point that project's `kilo.json` at this `skills/` directory, or
copy `skills/migration-guard/` into its skill directory.

## Use

Ask the agent to use the skill when reviewing a migration:

```text
Use the migration-guard skill to review db/migrate/20261116_add_index.sql.
Is this safe to deploy?
```

The skill tells the agent to read the schema and the application code before answering, to cite
`path:line` for every finding, to lint its own report, and to state what it could not inspect.

The report structure is not free-form. It is defined by
[`report-template.md`](skills/migration-guard/report-template.md) and checked by
`scripts/linter_report.py`:

```bash
python scripts/linter_report.py path/to/report.md
```

A report that fails the linter is a report the scorer cannot read, so a correct analysis in the wrong
shape scores as a miss. That is deliberate: an unattributable finding cannot be counted, gated, or diffed
between runs.

## Repository layout

```text
skills/migration-guard/
├── SKILL.md                        instructions the agent loads
├── report-template.md              the output contract, copied into every report
├── references/                     loaded on demand
│   ├── change-impact-taxonomy.md   reversibility, data loss, operational risk
│   ├── risk-classification.md      severity rules
│   ├── postgres.md                 locks, CONCURRENTLY, NOT VALID, rewrites
│   ├── mysql.md                    INSTANT/INPLACE/COPY, metadata locks
│   ├── safe-migration-patterns.md  expand-and-contract, shadow columns
│   └── rollback-strategy.md        recovery planning
└── examples/                       one conforming report and one rejected report, same migration
tests/migration-guard/              20 cases, frozen expectations, harness prompt
evals/results/                      recorded iterations with scorecards and provenance
docs/                               design, evaluation, compatibility, security model
scripts/                            validators, scorer, linter, eval runner, tests
```

## Scripts

All are standard-library Python, with no dependencies. Run them from the repository root.

| Command | What it does |
|---|---|
| `python scripts/validate_repo.py` | frontmatter, required files, 20 cases, expectation consistency, compat and Kilo discovery |
| `python scripts/test_scoring.py` | regression tests pinning every scorer and linter defect found in review |
| `python scripts/check_claims.py README.md` | rejects numbers in the README that do not point at `docs/evaluation.md` |
| `python scripts/linter_report.py <report.md>` | checks one report against the contract |
| `python scripts/evaluate_reports.py --reports <dir>` | scores reports against the frozen expectations |
| `python scripts/prepare_eval_workspaces.py` | builds one isolated directory per case, with no `expected/` visible |
| `python scripts/check_fixtures.py --postgres … --mysql …` | applies each fixture to a real database and asserts it fails or applies as documented |

`check_fixtures.py` needs `psql` and `mysql` clients and a running database; it runs in CI against
`postgres:16` and `mysql:8.0`.

## Principles

1. Evidence before assumptions — a finding without a citation is an unknown, not a finding.
2. Inspect before modifying — know what exists and who uses it before recommending a change.
3. Safe changes over quick changes — an instant `RENAME` can be a slower *change* than a four-step
   expand-and-contract.
4. Verification over confidence — every recommendation ships with a way to check it.
5. Human approval for destructive changes — an agent cannot approve its own data loss.
6. Never weaken a test to raise a score — if an expectation is wrong, fix it and say why in writing.

See [`docs/design-principles.md`](docs/design-principles.md) for the reasoning and
[`docs/security-model.md`](docs/security-model.md) for what the skill is allowed to do, and why an Agent
Skill is not a sandbox.

## Scope

**In scope (Phase 1):** database migration safety for PostgreSQL and MySQL.

**Out of scope:** API contract checks, authorization checks, behaviour preservation, release checks,
MariaDB / SQL Server / SQLite, and executing migrations. These are later phases, not omissions.

## License

MIT. See [`LICENSE`](LICENSE).