# Changelog

All notable changes to Reliant will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- Repository foundation: folder structure, MIT license, README, CONTRIBUTING.
- `migration-guard` skill: workflow, risk classification (BLOCKER / HIGH / MEDIUM / LOW / INFO), verdict rules
  (BLOCKED / CONDITIONAL / APPROVED), and evidence-based report contract.
- Reference documentation: risk classification, PostgreSQL specifics, MySQL specifics, safe migration
  patterns, rollback strategy. Engine claims verified against the PostgreSQL 18 manual, the MySQL 8.0
  manual, and the MySQL 8.0.12 / 8.0.28 / 8.0.29 release notes.
- Test suite: 16 positive cases (8 PostgreSQL, 8 MySQL) and 4 negative cases, each with `context.md`,
  `schema.sql`, `migration.sql`, `application-usage.md`, plus expected results in Markdown and
  machine-checkable JSON.
- Evaluation methodology and metrics (`docs/evaluation.md`). No runs recorded yet.
- Documentation: `docs/design-principles.md`, `docs/compatibility.md`, `docs/security-model.md`.
- Worked examples: a correct `APPROVED` report and a correct `BLOCKED` report.
- CI workflow (`.github/workflows/validate-skills.yml`) with four jobs: structure validation, pinned
  `skills-ref` (secondary, non-blocking), fixture verification against real `postgres:16` and
  `mysql:8.0` services, and documentation claim hygiene.
- Local tooling: `scripts/validate_repo.py`, `scripts/evaluate_reports.py`,
  `scripts/check_fixtures.py`, `scripts/check_claims.py`.
- Compatibility layers so supported agents can discover the skill:
  `.claude/skills/migration-guard` and `.agents/skills/migration-guard`.
- **`references/change-impact-taxonomy.md`** — the normative definitions every Reliant skill now shares:
  *effect on stored data* (additive / restrictive / narrowing / transformative / removing), *outcome*
  (applied / refused / partial), *reversibility*, *data loss*, and *operational risk* are five separate
  questions, each with a test. Approval class is derived from them, never from severity or from the
  operation's name. Later phases (API contract, authorization, behavior, release) reuse the same tables
  with their own definition of "stored data"; §7 maps them.
- **`scripts/linter_report.py`** — checks one report against the six-heading contract: the verdict token,
  the `### F<n> — SEVERITY — category` heading, all seven finding fields, a `path:line` citation on each
  risk finding, the approval class against reversibility, and leftover `<placeholders>`. Exit 1 on any
  deviation, with the line and the fix. `--reports DIR` lints a whole iteration.
- **`scripts/prepare_eval_workspaces.py`** — builds one isolated directory per case (the four fixture
  files, `report-template.md`, `review-prompt.md`) and asserts that no `expected/` artefact leaks in.
- **`skills/migration-guard/report-template.md`** — the single authoritative report contract, as a
  copy-paste skeleton.
- **`skills/migration-guard/examples/good-report.report.md` and `bad-report.report.md`** — the same
  migration reviewed twice: one conforming report and one rejected report, with each deviation explained
  beside it. Both are linted in CI.
- `scripts/test_scoring.py` — regression tests pinning every scorer and linter defect found by
  review: the destructive-policy denominator, bare-filename evidence, long-gap negated approvals, the
  format order check, and linter/scorer agreement. Run in CI.
- `evals/results/iteration-4/` — the first recorded iteration that meets every metric, with
  `iteration-meta.json` recording that it is **one run per case** and self-evaluated, so the number is
  not presented as a stable or independent result.
- Release verification: the compatibility layer is documented as inactive with a tested setup command,
  and `check_fixtures.py` now isolates each case in its own schema/database, parses `mysql://` URIs into
  client flags, and creates `citext` before applying PG-001. Those were the three causes of the failing
  GitHub Actions run.
- `README.md`: evaluation status now points at `docs/evaluation.md` instead of restating numbers, so the
  two cannot drift.

### Fixed

- `evaluate_reports.py`: forbidden-category severity comparison was inverted, so invented risk on
  negative cases was never counted as a false positive.
- **Scorer v0.2 — four measurement defects corrected, reports unchanged.** Evidence coverage passed
  when the fixture filename appeared *anywhere* in the report; it now requires the path citation
  *inside that finding*. Destructive policy was scored once per case; it is now scored per irreversible
  finding. `APPROVAL_RE` matched "human approval" inside "no human approval needed", so a correct
  `approval: none` failed; negated forms are excluded. Format compliance checked a three-heading subset
  that no document defined; it now checks the six headings the contract names. `case_dir` was never
  passed to `score_case`, so a fixture citation could not be resolved at all. Re-scoring iterations 1–2
  with the corrected scorer changes the numbers in both directions; every delta is justified in
  `docs/evaluation.md` §7.
- **`references/change-impact-taxonomy.md` added: reversibility, data loss, and operational risk are
  now three separate, defined questions.** They were previously collapsed into one word ("destructive"),
  which is what allowed a refused constraint to be described as data loss.
- **Five expected results corrected to follow that taxonomy.** `PG-003`, `PG-004`, `PG-005`, `MY-003`
  and `MY-004` are statements the engine *refuses* (duplicate rows, orphan rows, out-of-range values),
  so they are reversible and lose no data: `irreversible` is now `false` and the finding's approval is
  `none`, not `accept-risk`. `PG-008` and `MY-008` are `RENAME COLUMN`, which preserves every value, so
  their approval is `schedule` (deploy order), not `accept-risk`. The deprecated `destructive` field
  was removed from those files. All five `expected.md` files repeated the identical sentence "a human
  accepts the loss of the old values"; each now states what that specific operation actually does.
- **Report contract made enforceable.** `SKILL.md` declared six headings while its own embedded
  template used a different H1 and an `**Why it is risky**` field, the shipped examples used
  `**Approval required**` and unlisted categories, and the scorer checked three headings. There is now
  one `report-template.md`, a `scripts/linter_report.py` the agent runs on its own output, a conforming
  and a rejected example, and a CI check (`validate_repo.py`) that the examples match the contract.
- `validate_repo.py`: rejects duplicate finding ids, and rejects a fixture that declares
  `reversible: true` with `approval: accept-risk` (or the reverse), so the taxonomy cannot drift.
- `evaluate_reports.py`: a report containing emoji crashed the scorecard with `UnicodeEncodeError` on a
  Windows console; stdout/stderr are now UTF-8 with replacement.
- `evaluate_reports.py`: prose reports with `## 1. …` sections and no `## Findings` heading were parsed
  as having zero findings; they are now parsed for recall (format compliance still fails, as intended).

- `safe-migration-patterns.md`: the batched-backfill example was not portable. MySQL rejects
  `UPDATE ... WHERE id IN (SELECT ... FROM same_table ... LIMIT ...)` (`ERROR 1093`, and `LIMIT` is
  not accepted in that `IN` subquery on many versions). PostgreSQL and MySQL now have separate,
  runnable examples with explicit commit-per-batch behaviour, an idempotent predicate, and named abort
  conditions (replication lag, batch duration, error rate).
- `risk-classification.md`: severity could exceed the evidence. An unverified data property is now
  `HIGH` with the check query, never `BLOCKER`; a `BLOCKER` requires a proven consequence. Added an
  explicit ceiling table and a rule that human claims are not evidence.
- `mysql.md`: the file claimed to cover "MySQL or MariaDB-family" while citing only Oracle MySQL
  documentation. MariaDB is now explicitly out of scope, with a table of the divergences and a
  detection rule; an unknown engine caps the verdict at `CONDITIONAL`.
- Approval model: "human approval for any destructive operation or backfill" was too blunt. Replaced
  with two concepts — approval to execute (host permission) and approval to accept risk (human). The
  class is decided by reversibility (`none` / `schedule` / `accept-risk`), not by the operation type.
  Fixtures, `expected-schema.json`, and both scripts follow the new model, and an over-cautious
  approval on a reversible change is now scored as a miss.

### Known limitations

- 20 cases cover the Phase 1 scope (PostgreSQL and MySQL only). SQL Server, SQLite, and MariaDB
  are out of scope for v0.1.0.
- No automated application-code dependency map. Evidence about application usage must come from what the
  agent can inspect in the repository.
- **No recorded iteration meets the release targets.** Iterations 1–2 have committed evidence and
  neither reaches the targets. An iteration-3 that did reach them was destroyed by a bug in
  `prepare_eval_workspaces.py` before it was committed, so its result is void and is not quoted; see
  `docs/evaluation.md` §7.
- **No independent evaluation yet.** Every recorded run is self-evaluated: the cases were written by the
  same people as the skill. v0.1.0 stays unreleased until a run on a different model family is recorded.
- Iterations 1–2 have three runs per case each; any future iteration should match that so a single run
  is not mistaken for a distribution.
- The v0.2 scorer changed how evidence and destructive policy are counted. Iterations 1–2 were
  re-scored with it and their committed scorecards regenerated; the reasons for each delta are in
  `docs/evaluation.md` §7. A scorer change that lowers an old score is recorded, not hidden.

## [0.1.0] - TBD

First public release. Released only when the Phase 1 release readiness checklist passes. As of
2026-11-18 the open criteria are evidence-related, not code-related; see the table in
`docs/evaluation.md` §7.
