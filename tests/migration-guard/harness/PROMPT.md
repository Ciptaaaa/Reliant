# Migration Guard — test harness prompt

Use this template verbatim for every eval run, with `{{CASE_ID}}` and the file list substituted. Keep
the framing identical across models so results are comparable.

---

## Run prompt (positive and negative cases)

```text
You are reviewing a database migration before it is merged.

Case: {{CASE_ID}}
Working directory files for this case:
- context.md
- schema.sql
- migration.sql
- application-usage.md

Use the migration-guard skill to analyze `migration.sql`. I want to know whether this is safe to
deploy. Follow the skill's report format exactly, quote the evidence you rely on, and tell me the
verdict.
```

## Baseline prompt (no skill)

Same prompt without the sentence "Use the migration-guard skill…". Used to measure whether the skill
adds value over an unaided agent.

## What the harness records per run

| Field | Example |
|---|---|
| `case_id` | `PG-001` |
| `model` | exact model identifier |
| `agent` | product + version (e.g. Claude Code 1.x, Copilot in VS Code 1.x) |
| `date` | ISO date |
| `run` | 1, 2, or 3 |
| `skill_version` | from `CHANGELOG.md` / SKILL.md metadata |
| `with_skill` | true/false |
| `report_path` | `evals/results/iteration-N/PG-001/report.md` |
| `duration_s`, `tokens` | if the agent reports them |

Save `run-meta.json` next to each report. The evaluator only reads `report.md`, so extra files are
harmless.

## Rules

1. Do not modify the case files between runs.
2. Do not add hints ("by the way, check if the column is used").
3. Run each case in a fresh session, no conversation carry-over.
4. If the agent refuses to produce a report, save its response verbatim as `report.md` — it will
   score as a miss, which is the correct outcome.
5. Record the full transcript when possible; the transcript is what tells you *why* a finding was
   missed.
