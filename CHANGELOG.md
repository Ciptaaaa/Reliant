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

### Fixed

- `evaluate_reports.py`: forbidden-category severity comparison was inverted, so invented risk on
  negative cases was never counted as a false positive.
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

- 16 cases cover the Phase 1 scope (PostgreSQL and MySQL only). SQL Server, SQLite, and MariaDB
  are out of scope for v0.1.0.
- No automated application-code dependency map. Evidence about application usage must come from what the
  agent can inspect in the repository.
- The evaluation method and metrics are documented, but **no model has been run against the suite**.
  No quality number may be claimed in the README before results are recorded in `docs/evaluation.md`.

## [0.1.0] - TBD

First public release. Released only when the Phase 1 release readiness checklist passes
(see `README.md`).
