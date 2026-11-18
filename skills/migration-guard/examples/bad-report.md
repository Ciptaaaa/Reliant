# BAD — a report that is rejected

This page exists to be linted. The rejected report is **not** on this page: it is in
[`bad-report.report.md`](bad-report.report.md), a standalone file so that CI can assert it fails while
this page stays a normal document.

```bash
python scripts/linter_report.py skills/migration-guard/examples/bad-report.report.md   # exits 1
```

`good-report.report.md` covers the same migration and reaches the same conclusion. Only the structure
differs. The analysis in the bad report is largely correct; it is rejected anyway, and each
`REJECTED:` note beside it says exactly why. This is the most common failure mode in the recorded
evaluations: the right answer in the wrong shape.

## What the bad report does, mapped to what the contract wants

| What the bad report did | What the contract wants |
|---|---|
| `# Migration Guard Report — CASE-EXAMPLE-001` | `# Migration Guard Report`, case ID in `Scope → Migration` |
| `## Verdict: ⛔ BLOCK MERGE` | `## Verdict` then `**BLOCKED** — one sentence` |
| `## Evidence` with `### 1. …` prose | `## Findings` with `### F1 — BLOCKER — destructive-column-drop` |
| No field list | All seven `- **Field**:` lines per finding |
| `## Blast radius` | The `**Impact**:` line of the relevant finding |
| `## Recommended fix` | `## Safer migration path`, numbered, with syntax |
| `## Summary` | Nothing. A summary is a finding's `Impact` restated; the verdict already says it. |
| No rollback, no checklist | `## Rollback and recovery` and `## Pre-deploy checklist` |

## The lesson

These two reports say the same thing. Only one of them can be checked, diffed between runs, counted,
and gated before merge. When a report is rejected, the useful question is never "was the model wrong?"
but "is this finding addressable, and is its evidence openable?"
