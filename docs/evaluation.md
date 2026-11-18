# Evaluation

How Reliant's analytical quality is measured, and what has actually been measured so far.

**Current status: evaluated, and below target.** Two iterations were run on 2026-11-18 against
`deepseek-v4.1-flash`. The skill **fails** its own release criteria — most importantly format
compliance is 0% and required finding recall is 52–64% against a 90% target. See §7 for the full
results and the remediation required before v0.1.0.

The run is **self-evaluated**: the fixtures were written by the same model family being tested. It
measures internal consistency, not independent quality, and must not be quoted as a capability claim.

## 1. What is being measured

A Migration Guard report can fail in four ways, and the suite must catch all four:

| Failure | Example | Metric that catches it |
|---|---|---|
| Missed critical risk | Approves a drop of a column that is still read | Critical finding recall |
| Missed secondary risk | Finds the drop, misses that it is irreversible | Required finding recall |
| Invented risk | Claims a rewrite for a metadata-only add | False positive rate |
| Unsupported claim | Right severity, no citation | Evidence coverage |

Two further failures concern the report as a process artefact rather than its analysis: no approval gate
on a destructive change, and a verdict that does not follow from the findings.

## 2. Metrics and targets

| Metric | Definition | Target | Source |
|---|---|---|---|
| Critical finding recall | Share of findings with `severity: BLOCKER` (`critical: true`) that the report contains | **100%** | `required_findings` where `critical` is true |
| Required finding recall | Share of all `required_findings` the report contains | **≥ 90%** | `required_findings` |
| False positive rate | Share of findings reported on negative cases that are not required; plus forbidden-category hits | **≤ 20%** | `forbidden_findings`, negative cases |
| Evidence coverage | Share of required findings whose `must_cite_evidence` files are cited in the report | **100%** | `must_cite_evidence` |
| Destructive action policy compliance | Share of destructive cases whose report states a human approval gate | **100%** | `destructive: true` |
| Verdict accuracy | Share of runs whose verdict is in `allowed_verdicts` | **≥ 90%** | `allowed_verdicts` |

These are targets, not results. A run that misses a target is recorded as a miss; the targets are not
lowered to match the run.

### How a finding is counted as "found"

`scripts/evaluate_reports.py` matches a reported finding to an expected one when both hold:

1. The severity matches exactly (`HIGH` reported as `MEDIUM` is a miss — severity is part of the claim).
2. The finding's category matches, **or** one of the expected `must_mention_any` phrases appears in the
   finding text.

Matching on phrases rather than only on categories keeps the scorer tolerant of structure differences
between models while still requiring the substance. When a run is correct but fails to match, fix the
phrase list for that case — not the report.

### Why only `BLOCKER` counts toward critical recall

`HIGH` and `MEDIUM` misses are real but recoverable by a human reviewer; a missed `BLOCKER` means data
loss or a broken deploy got through. Keeping the strict metric narrow keeps it meaningful.

## 3. Test set

20 cases: 8 PostgreSQL, 8 MySQL, 4 negative. The full list, with the expected risk and verdict, is in
`tests/migration-guard/README.md`.

Design constraints:

- Every case has a stated engine **and version**, because most of the risk depends on it.
- Every case contains evidence the agent can actually find, and no evidence that does not exist.
- Positive cases deliberately include user assertions that are wrong (PG-001 "it is unused", MY-004
  "orders cascade"), because accepting a confident claim is the most common real-world failure.
- Negative cases exist to punish invented risk. A skill that flags everything scores 100% recall and
  fails this project's purpose.

## 4. Protocol

Run each case in a **fresh session**, with only the four case files visible:

1. Copy `cases/<group>/<ID>/` to a scratch workspace. Do not include `expected/`.
2. Send the prompt from `tests/migration-guard/harness/PROMPT.md`, with the case ID substituted.
3. Save the reply verbatim as `evals/results/iteration-N/<ID>/report.md`.
4. Optionally run the same case with the skill disabled, saving to a `baseline/` directory, to check
   that the skill adds value.
5. Repeat each case **at least three times** per model, because output is nondeterministic. Run 1–3
   can be separate iterations or `run-1/`, `run-2/`, `run-3/` subdirectories; score each iteration
   separately.
6. Record `run-meta.json` beside each report: model, agent and version, date, run number,
   `skill_version`, whether the skill was enabled, duration, and token usage.

Score:

```bash
python scripts/evaluate_reports.py --reports evals/results/iteration-1 --json evals/results/iteration-1/scorecard.json
```

The script exits non-zero when a metric misses its target or a case has no report.

## 5. First-run expectations

Predictions before running, written down so they can be checked against reality:

- **Critical recall will be high on the obvious cases** (PG-001, PG-003, PG-004, MY-001, MY-003,
  MY-004) because the evidence is explicit in the fixtures.
- **The likely misses are the version-boundary cases**: MY-005 (INSTANT does not apply to a type change)
  and MY-006 (INSTANT only allows `LOCK=DEFAULT`). A model relying on memory rather than the reference
  file is likely to get the mechanism wrong.
- **The likely false positives are NEG-004** (new column with `NOT NULL DEFAULT` looks risky but is
  metadata-only) **and PG-007 / MY-007** (adding a column invites "backfill needed" advice that the
  fixtures forbid).
- **Evidence coverage will be the weakest metric** because reports tend to summarise evidence instead
  of citing a path.

If the first run contradicts these predictions, the fixture is as likely to be wrong as the model. Fix
whichever is wrong, and note it in section 7.

## 6. Iteration loop

1. Run all 20 cases, three times.
2. Grade and aggregate.
3. Read the failing cases' **transcripts**, not only the reports. A miss caused by the agent never
   opening `application-usage.md` is an instruction problem; a miss caused by the agent reading it and
   drawing the wrong conclusion is a reference-content problem.
4. Change the smallest thing that addresses the cause: a workflow step, a gotcha, a reference
   paragraph, or — if the expectation itself was unreasonable — the fixture, with a note.
5. Rerun in a new iteration directory and record the delta.

Stop iterating when critical recall is 100%, no metric is below target, and a reviewer reading the
reports would accept them as the analysis a careful engineer would produce.

## 7. Results log

### 2026-11-18 — deepseek-v4.1-flash (served via local proxy alias `bansos_codebuddy`) — skill v0.1.0

- **Independence: self-evaluated, not independent.** The fixtures were written by the same model family
  being tested. These numbers measure internal consistency against this suite, not general quality.
  They must not be quoted as a capability claim.
- **Mode**: single-turn chat completion, no tools, all references preloaded. This differs from a real
  Agent Skills session (which loads `references/` on demand and can use `Read`/`Grep`). See §8.
- Runs: 20 cases × 3, `evals/results/iteration-1` and `iteration-2`.
- Scorecards: `evals/results/iteration-*/scorecard.json`

| Metric | Target | iteration-1 | iteration-2 | Met? |
|---|---|---|---|---|
| Critical finding recall | 100% | 66.7% | 94.4% | **No** |
| Required finding recall | ≥ 90% | 51.9% | 63.6% | **No** |
| False positive rate | ≤ 20% | 0.0% | 37.5% | iteration-1 only |
| Evidence coverage | 100% | 18.6% | 20.2% | **No** |
| Destructive policy compliance | 100% | 23.8% | 9.5% | **No** |
| Verdict accuracy | ≥ 90% | 55.0% | 60.0% | **No** |
| Format compliance | ≥ 90% | 0.0% | 0.0% | **No** |

**Verdict: the skill does not meet its own release criteria.** It must not be released as v0.1.0.

### What the failures actually are

**Format compliance is 0% across all 60 runs.** Every report used a different structure than the
contract: `## Migration Guard Report` plus `### Analysis` / `### Evidence`, with severity in a
`**Verdict: ...**` line instead of `### F1 — SEVERITY — category` headings. The instruction "emit
exactly these six headings" was not followed in a single run. A format that can be ignored 60 times
out of 60 is not a contract; it is a suggestion.

**Recall misses are mostly attribution, not analysis.** In the sampled reports the model identified the
correct risks and reached the correct verdict, but attached severity and category in prose. Because
per-finding severity is what the recall metrics are built on, a correct analysis scores as a miss. This
is a real defect in the skill: the report format exists so results are comparable, and it is not
working.

**Destructive policy compliance is low** because the model rarely states an explicit approval class
(`none` / `schedule` / `accept-risk`). It discusses the risk, then omits the decision field.

**Negative cases are the one clean signal.** All 12 negative runs in iteration-1 returned `APPROVED`
with no invented risk. The skill does not manufacture risk on safe migrations, which was the primary
false-positive concern when the suite was designed.

### What changed between iterations

Iteration-2 reordered the system prompt so `SKILL.md` comes **after** the references, on the theory
that 60K of reference material after the instruction file buries the report contract. Critical recall
improved 66.7% → 94.4% and required recall 51.9% → 63.6%, but format compliance stayed at 0% and the
false-positive rate rose 0% → 37.5%. The reordering helps recall and hurts precision; it is not a fix
for the format failure.

### What must change before release

1. **Make the format enforceable, not requested.** Either ship a template the agent must fill (so a
   missing heading is a visible hole, not an alternative structure), or add a post-processing step that
   rejects a non-conforming report. Prose instructions have now failed 60/60.
2. **Re-run after (1).** The current recall numbers are unknown-correct: they cannot separate "did not
   find the risk" from "found it and did not format it". Fixing the format should raise required recall
   without any change to the analysis, which is itself the test of whether (1) worked.
3. **Do not lower the targets.** 100% critical recall is the point of the project.

### Cases that could not be run

None. All 20 cases produced a report in both iterations.

## 8. Limitations of this evaluation

- **The suite was written by the same people who wrote the skill.** Cases the authors did not think of
  are not tested. Fixtures are a floor, not a ceiling.
- **Fixtures are cleaner than reality.** Real repositories have partially known schemas, inconsistent
  naming, and consumers outside the repository. The `application-usage.md` "not searched" sections model
  this, but thinly.
- **The scorer is tolerant by design.** Phrase matching means a semantically wrong finding that uses the
  right word can pass. Human review of reports is part of the protocol for this reason.
- **Twenty cases cannot cover SQLite, SQL Server, MariaDB, partitioned tables, logical replication, or
  schema-change tools.** Absence of a failure here is not evidence of correctness there.
- **A passing recall score is not a safety guarantee.** It means the skill found what this suite
  contains. Production safety still requires human approval, backups, and a tested rollback.