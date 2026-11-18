# Contributing to Reliant

Thanks for considering a contribution. Reliant is a collection of Agent Skills for safer software
changes. The quality bar is deliberately high: a skill that produces a convincing but unsupported
report is worse than no skill at all.

## Ground rules

1. **Evidence before assumptions.** Every claim a skill asks an agent to make must have a way to
   cite evidence (file, line, or query).
2. **No undocumented claims.** Technical statements in `references/` must be verifiable against
   official PostgreSQL or MySQL documentation. Add the doc URL or a version qualifier.
3. **Scope discipline.** Phase 1 covers database migration safety for PostgreSQL and MySQL only.
   Do not add new skills or engines without an accepted proposal in an issue.
4. **No secrets, no production credentials, no execution of real migrations.**

## Repository layout

```
skills/migration-guard/       Source of the skill (SKILL.md + references + examples)
tests/migration-guard/        Test cases and expected results
docs/                         Design, evaluation, compatibility, security model
scripts/                      Local validators and eval tooling
evals/                        Eval run workspace (gitignored results)
.claude/skills/               Compat layer for Claude Code (points to skills/)
.agents/skills/               Compat layer for VS Code / Copilot (points to skills/)
```

`skills/` is the single source of truth. The compat layers must not diverge from it; they are
symlinks (or directories containing `symlink.txt` when symlinks are unavailable on Windows). CI
verifies that compat targets resolve to the same `SKILL.md`.

## Changing a skill

1. Read the [Agent Skills specification](https://agentskills.io/specification).
   The `name` must match its folder, lowercase alphanumeric + hyphens, and `description` must be
   at most 1024 characters.
2. Keep `SKILL.md` under 500 lines and 5,000 tokens. Move detailed knowledge into `references/`
   and tell the agent **when** to load each file.
3. Update or add a test case for any behavior change. A change without a test case will be
   rejected.
4. Update `CHANGELOG.md` under `[Unreleased]`.

## Adding a test case

A case lives in `tests/migration-guard/cases/<postgres|mysql|negative>/<ID>/` and contains:

| File | Purpose |
|---|---|
| `context.md` | Engine, version, deployment model, and what the user asked |
| `schema.sql` | Current schema state (the starting point) |
| `migration.sql` | The change under review |
| `application-usage.md` | Evidence about how application code uses the affected objects |

The matching `tests/migration-guard/expected/<...>/<ID>/` contains:

| File | Purpose |
|---|---|
| `expected.md` | Human-readable expected analysis |
| `expected.json` | Machine-checkable expectations used by `scripts/evaluate_reports.py` |

IDs follow `PG-NNN` and `MY-NNN`, and `NEG-NNN` for negative cases.

`expected.json` must conform to `tests/migration-guard/expected-schema.json`. Severity values are
one of `BLOCKER`, `HIGH`, `MEDIUM`, `LOW`, `INFO`. Verdicts are one of `BLOCKED`, `CONDITIONAL`,
`APPROVED`.

Severity semantics (Phase 1, frozen):

| Level | Meaning |
|---|---|
| `BLOCKER` | Change is unsafe based on evidence (data loss or breakage is proven in scope) |
| `HIGH` | Large risk to data or operations (likely data loss, rewrite, blocking lock on a large table) |
| `MEDIUM` | Risk that requires mitigation (backfill, extra coordination) |
| `LOW` | Relatively small risk (e.g. adding a nullable column) |
| `INFO` | Additional information; no risky finding in the inspected scope |

`critical: true` in `expected.json` marks the findings counted in **critical finding recall**.
That metric counts only findings whose severity is `BLOCKER`.

## Validating before you push

```bash
python scripts/validate_repo.py       # structure, spec conformance, fixture consistency
python scripts/check_claims.py README.md   # no unsubstantiated performance claims
```

If Docker or a local database is available, also prove the fixtures still reproduce:

```bash
python scripts/check_fixtures.py \
  --postgres "postgresql://postgres:pw@127.0.0.1:5432/postgres" \
  --mysql    "mysql://root:pw@127.0.0.1:3306/mysql"
```

CI runs all three. `validate_repo.py` and `check_claims.py` are blocking; the pinned `skills-ref`
validator is a secondary, non-blocking check because upstream describes it as a demonstration library.

## Compat layers

`.claude/skills/migration-guard` and `.agents/skills/migration-guard` must resolve to
`skills/migration-guard`. They are symlinks on Linux/macOS and git checkouts with `core.symlinks=true`.
On Windows without symlink support, each contains a `symlink.txt` that names the target and the command
to create a working link. Never copy files into the compat layers; `validate_repo.py` fails if they
diverge.

## Evaluation contributions

If you run evaluations, record the model name, agent version, date, and the raw reports. Do not
publish aggregate numbers in the README. Put them in `docs/evaluation.md` section 7 (Results log)
together with the run metadata, and keep any claim in the README pointing at that file.

## Commit messages

Use the imperative mood and a short subject. Reference the affected area:

```
migration-guard: classify NOT NULL without backfill as HIGH
tests: add MY-009 for changing column collation
docs: clarify rollback vs data recovery
```

## License

By contributing, you agree that your contribution is licensed under the MIT License.
