# Design Principles

Why Reliant exists, and the rules that keep it from becoming another confident-sounding prompt.

Tagline: **Prove your changes are safe.**

## 1. The problem

A coding agent asked "is this migration safe?" will answer. It will answer even when it has not read the
application code, does not know the table size, and cannot see that a second application version is
still running. The answer is fluent, plausible, and occasionally catastrophic.

The failure mode is not incompetence. It is **unearned confidence**: the model produces the shape of an
analysis without the substance of one, and nothing in the output reveals the difference.

Reliant's job is to make that difference visible and hard to fake.

## 2. Five principles

### Evidence before assumptions

Every severity must cite something: a code path at `path:line`, a row count in the schema file, a
documented engine behaviour. A claim without a citation is recorded as an **unknown**, not as a finding.

The user saying "that column is unused" is a claim, not evidence. The column still being read at
`app/controllers/users_controller.rb:31` is evidence, and it wins.

### Inspect before modifying

The schema, the migration, and the application code are read before anything is recommended. The agent
does not propose `DROP COLUMN` or `ADD CONSTRAINT` until it knows what currently exists and who uses it.

### Safe changes over quick changes

A one-line `RENAME COLUMN` that takes a millisecond is worse than a four-deploy expand-and-contract
sequence that never breaks a running version. Speed of the statement is not speed of the change.

### Verification over confidence

Every recommendation ships with the way to check it: the query that proves no orphans remain, the
`indisvalid` check after a concurrent index build, the reconciliation count after a backfill. A
mitigation that cannot be verified is a suggestion.

### Human approval for destructive changes

Any operation that destroys data or breaks a running deployment requires a named human decision, a
recovery path, and a record that both exist. An agent cannot approve its own data loss.

## 3. What makes this different from a long prompt

| A careful prompt | Reliant |
|---|---|
| Says "be careful with migrations" | Defines five severity levels with explicit entry criteria |
| Lists risks | Requires a citation per risk and downgrades uncited claims to unknowns |
| Recommends best practices | Ships a fixed report contract so results are comparable across runs |
| Cannot be tested | Has 20 fixtures, a schema for expected results, and a scoring script |
| Claims quality | Reports measured recall per model, per date, or reports nothing |

The fourth row is the important one. A skill without a test suite is an opinion; the fixtures are what
make it a claim that can be checked.

## 4. Design decisions and their trade-offs

### Severity is frozen at five levels

`BLOCKER`, `HIGH`, `MEDIUM`, `LOW`, `INFO`. More levels invite arguments about `CRITICAL` vs `HIGH`;
fewer collapse the distinction that matters (proven unsafe vs merely expensive). Verdicts are derived
from the worst finding by a stated rule, so two people applying the rubric reach the same verdict.

### Only `BLOCKER` counts toward critical recall

The evaluation treats `BLOCKER` as the "must never miss" class, because it is the class that means
"this change will destroy data or break running code". `HIGH` misses are measured by the broader recall
metric. This keeps the strict metric honest instead of lumping everything important into it.

### The report has a fixed shape

A free-form report is ungradeable. The contract (Scope, Verdict, Findings, Safer path, Rollback,
Checklist) exists so `scripts/evaluate_reports.py` can read it and so a human reviewer knows where to
look. Findings carry a `category` from a closed list so cross-run comparison is possible.

### `APPROVED` is narrowly scoped

It means "no blocker found in the inspected scope with the available evidence". Every approved report
repeats that sentence. This is deliberate: the verdict is the most dangerous output the skill can
produce, so it is defined as weakly as possible while still being useful.

### The skill does not execute anything

No database connection, no migration runner, no `EXPLAIN`. Analysis is repository-only. This costs
fidelity — lock durations and row counts are often unknown — and the design accepts that cost by
requiring unknowns to be listed rather than estimated. The alternative, a skill that needs database
credentials, trades a small fidelity gain for a large and permanent security surface.

### One skill, not five

Phase 1 ships exactly one skill. Five shallow skills would be easier to demo and worse at their job.
The next guards (API contract, authorization, behaviour preservation) wait until this one has measured
results.

## 5. Non-goals

- Not a migration runner, a linter, or a linter replacement.
- Not a guarantee of production safety. It finds blockers; it does not prove absence of risk.
- Not a replacement for code review, testing, or a change-approval process.
- Not a benchmark marketing asset. Any number in this repository points at a run that can be repeated.