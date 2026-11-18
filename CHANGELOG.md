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
  patterns, rollback strategy.
- Test suite: 16 positive cases (8 PostgreSQL, 8 MySQL) and 4 negative cases, each with `context.md`,
  `schema.sql`, `migration.sql`, `application-usage.md`, plus expected results in Markdown and JSON.
- Evaluation methodology and metrics (`docs/evaluation.md`).
- CI workflow validating Agent Skills format, required files, and test fixture completeness
  (`.github/workflows/validate-skills.yml`).
- Local validators: `scripts/validate_repo.py` (spec + repo completeness),
  `scripts/evaluate_reports.py` (metrics from agent reports).
- Compatibility layers so supported agents can discover the skill:
  `.claude/skills/migration-guard` and `.agents/skills/migration-guard`.

### Known limitations

- 16 cases cover the Phase 1 scope (PostgreSQL and MySQL only). SQL Server, SQLite, and MariaDB
  are out of scope for v0.1.0.
- No automated application-code dependency map. Evidence about application usage must come from what the
  agent can inspect in the repository.
- `docs/evaluation.md` documents the method and metrics. Baseline results are recorded per model, agent
  version, and date, and must not be claimed in the README before they are run.

## [0.1.0] - TBD

First public release. Released only when the Phase 1 release readiness checklist passes
(see `README.md`).
