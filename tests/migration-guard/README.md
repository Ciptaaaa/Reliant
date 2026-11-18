# Test Suite: Migration Guard

This suite answers one question objectively: **does Migration Guard find the risks it is supposed to
find, without inventing risks that are not there?**

## Layout

```
tests/migration-guard/
├── README.md                     this file
├── expected-schema.json          JSON Schema for every expected.json
├── harness/
│   └── PROMPT.md                 the exact prompt template used for each run
├── cases/
│   ├── postgres/PG-001..008/     context.md, schema.sql, migration.sql, application-usage.md
│   ├── mysql/MY-001..008/
│   └── negative/NEG-001..004/
└── expected/
    ├── postgres/PG-001..008/     expected.md + expected.json
    ├── mysql/MY-001..008/
    └── negative/NEG-001..004/
```

## The 20 cases

| ID | Scenario | Expected risk | Expected verdict |
|---|---|---|---|
| PG-001 | Drop a column that application code still uses | `BLOCKER` | `BLOCKED` |
| PG-002 | `SET NOT NULL` while `NULL` rows exist | `HIGH` | `CONDITIONAL` |
| PG-003 | `UNIQUE` constraint with duplicate rows | `BLOCKER` | `BLOCKED` |
| PG-004 | `FOREIGN KEY` with orphan rows | `BLOCKER` | `BLOCKED` |
| PG-005 | Type change that truncates values | `HIGH` | `CONDITIONAL` |
| PG-006 | `CREATE INDEX CONCURRENTLY` inside a transaction | `HIGH` | `CONDITIONAL` |
| PG-007 | Add a nullable column | `LOW` | `APPROVED` |
| PG-008 | Rename a column while the old app version runs | `HIGH` | `CONDITIONAL` |
| MY-001 | Drop a column that application code still uses | `BLOCKER` | `BLOCKED` |
| MY-002 | `NOT NULL` on a column that still has `NULL` rows | `HIGH` | `CONDITIONAL` |
| MY-003 | `UNIQUE` index with duplicate rows | `BLOCKER` | `BLOCKED` |
| MY-004 | `FOREIGN KEY` with orphan rows | `BLOCKER` | `BLOCKED` |
| MY-005 | Online DDL with an algorithm the operation does not support | `HIGH` | `CONDITIONAL` |
| MY-006 | Table rebuild that takes a metadata lock | `HIGH` | `CONDITIONAL` |
| MY-007 | Add a nullable column with instant DDL | `LOW` | `APPROVED` |
| MY-008 | Rename a column with an application-compatibility problem | `HIGH` | `CONDITIONAL` |
| NEG-001 | Add a nullable column, touch nothing else | `LOW` | `APPROVED` |
| NEG-002 | Create an index that the code actually uses | `LOW` | `APPROVED` |
| NEG-003 | Create a table with no consumers yet | `LOW` | `APPROVED` |
| NEG-004 | Add a constant default via the safe path | `LOW` | `APPROVED` |

Severity values follow the five-level scale in `CONTRIBUTING.md`. `critical` = `BLOCKER` only.
The metrics are computed by `scripts/evaluate_reports.py`; the method and targets are in
`docs/evaluation.md`.

## How a case is run

1. Copy one case directory to a scratch workspace.
2. Send the prompt from `harness/PROMPT.md` with the case ID substituted.
3. The agent must be able to read the four files. **Do not** give it `expected/`.
4. Save the report to `evals/results/iteration-N/<CASE-ID>/report.md`.
5. Score: `python scripts/evaluate_reports.py --reports evals/results/iteration-N`.
6. Repeat each case at least 3 times per model/agent. Save runs as `run-1/`, `run-2/`, `run-3/`
   containing `report.md`, or run separate iterations per run.

## Rules for the harness

- No hints in the prompt beyond what a developer would normally say. The prompt says what changed and
  asks if it is safe to deploy.
- No `expected/` content in the prompt or workspace.
- The case files are the only evidence. If the agent asks for access it does not have, that is a
  finding about the skill (it should record an Unknown), not something the harness should fix
  mid-run.
- Weakening a case to make the agent pass is not allowed. If a case is wrong, change it and bump the
  changelog.

## Adding a case

Follow `CONTRIBUTING.md`. Every case needs:

- Four source files with **internal consistency**: schema + migration + usage references the same
  table and column names, and the usage file has real file:line citations.
- `expected.md`: the analysis a correct reviewer writes (findings, evidence, mitigation, verdict,
  unknowns).
- `expected.json`: validated against `expected-schema.json` by `scripts/validate_repo.py`. Every
  `must_cite_evidence` path must exist in the case directory.
- At least one `INFO` finding in `expected.md` listing what is safe, so the report can distinguish
  "checked and safe" from "not checked".

## Interpreting results

| Metric | Source | Meaning |
|---|---|---|
| Critical finding recall | required findings with `critical: true` | Must be 100%. Missing a `BLOCKER` is the failure that matters. |
| Required finding recall | all `required_findings` | ≥90% |
| False positive rate | forbidden categories on negative cases | ≤20% |
| Evidence coverage | `must_cite_evidence` in reports | 100% |
| Destructive policy compliance | `destructive: true` cases with an approval gate | 100% |
| Verdict accuracy | report verdict in `allowed_verdicts` | ≥90% |

A report that is correct in substance but uses different wording can still pass, because matching is
by category and by `must_mention_any` phrases. When a phrase match fails even though the semantics
are right, fix the phrase list, not the report.
