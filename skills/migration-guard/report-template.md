# Migration Guard — report template (fill this in, do not restyle it)

**Read this file before writing the report, and lint the report before sending it.**

This is not an illustration. It is the output contract. A report that does not fill *this* structure
is rejected by tooling even when its analysis is correct, so a correct answer in the wrong shape scores
as a failure. Six headings, in this order, nothing added, nothing renamed.

Two things make compliance easy to hit mechanically:

1. **Copy the skeleton below into your answer and replace every `<...>`.**
2. **Delete no heading and invent no heading.** Every `##` you write must be one of the six.

---

## The skeleton

<!-- COPY FROM HERE -->

````markdown
# Migration Guard Report

## Scope
- **Migration**: <the file path you reviewed, exactly as given>
- **Engine**: <PostgreSQL 16 | MySQL 8.0 | unknown>
- **Inspected**: <the files and directories you actually read>
- **Not inspected**: <external services, other repositories, mobile clients, dashboards>
- **Unknowns**: <missing version, unknown row counts, unknown deploy model — or `none material`>

## Verdict
**<BLOCKED | CONDITIONAL | APPROVED>** — <one sentence naming the decisive finding>

## Findings

### F1 — <BLOCKER | HIGH | MEDIUM | LOW | INFO> — <one category from the list below>
- **Statement**: `<the SQL statement>` in `<path>:<line>` — <what it does to the schema>
- **Evidence**:
  - `<path>:<line>` — <what is there; quote the code or the data fact>
  - `<path>:<line>` — <the second citation this finding needs>
- **Impact**: <the concrete consequence if this ships as written>
- **Mitigation**: <the safe alternative, or the precondition that must already be true>
- **Verification**: <the command or query a human runs to confirm the mitigation worked>
- **Approval**: <none | schedule | accept-risk>
- **Reversible**: <yes | no>

### F2 — <SEVERITY> — <category>
- **Statement**: ...
- **Evidence**:
  - <path:line> — ...
- **Impact**: ...
- **Mitigation**: ...
- **Verification**: ...
- **Approval**: <none | schedule | accept-risk>
- **Reversible**: <yes | no>

## Safer migration path
1. <first ordered step, with the engine-specific syntax the user needs>
2. <next step>

If the migration is already the safe form, write exactly: `Not needed. This migration is already the
additive, metadata-only form.` and say why the statement is cheap.

## Rollback and recovery
- **Schema rollback**: <what reverses the structure, and whether the reverse is clean>
- **Data recovery**: <what happens to rows this migration changed or deleted>
- **Application rollback**: <whether the previous application version works against the new schema>

## Pre-deploy checklist
- [ ] <a case-specific check>
- [ ] <another check>
````

<!-- COPY TO HERE -->

---

## The rules that decide format compliance

| # | Rule | Why it exists |
|---|---|---|
| 1 | Exactly **six** `##` headings: `Scope`, `Verdict`, `Findings`, `Safer migration path`, `Rollback and recovery`, `Pre-deploy checklist`, in that order. | A parser keys on these six names. A `## Summary` is not a section — it is a missing section plus an extra one. |
| 2 | The H1 is exactly `# Migration Guard Report`. No case ID, no filename, no date in the title. | The title is not content; put the file path in `Scope`. |
| 3 | The verdict is `**BLOCKED**`, `**CONDITIONAL**` or `**APPROVED**` in bold, **first thing** in `## Verdict`. | `**Verdict: BLOCK**`, `⛔ BLOCK MERGE`, and `DO NOT MERGE` are not the contract's tokens. Folding them in as synonyms is a tolerance, not a licence. |
| 4 | Every finding heading is `### F<n> — SEVERITY — category` with an em dash and one category from the list. Severity lives **in the heading**, not in a `**Risk:**` line. | This is the one field every metric is built on. When it is missing, a correct analysis is indistinguishable from no analysis. |
| 5 | Every finding carries all seven fields, on lines starting `- **Field**:`. | A finding without `Approval` cannot be gated; without `Evidence` it is an Unknown. |
| 6 | `APPROVED` always repeats the scope sentence. | `APPROVED` means *no blocker found in the inspected scope*, never "safe in production". |

### Categories (use exactly one per finding)

`destructive-column-drop`, `destructive-table-drop`, `type-change`, `not-null-add`,
`unique-constraint`, `foreign-key`, `index-build`, `lock-and-rewrite`, `backfill`, `rename`,
`charset-collation`, `implicit-commit`, `application-compatibility`, `data-integrity`, `operational`,
`no-risk-observed`.

A clean migration gets exactly one finding, `### F1 — INFO — no-risk-observed`, listing what you
checked and found safe. A report with no findings at all is a failure, not a pass.

### Operational risk belongs in a finding, not only in the safer path

A lock, rewrite, backfill, replica lag, or metadata-lock wait is a **finding** with a severity and an
`Approval` of `schedule` — not a remark buried in `## Safer migration path`. Reporting it as a finding
is what makes the window and the abort condition reviewable; leaving it to the checklist means nobody
gates it.

Two findings may share a severity and a category. They are different mechanisms and each needs its own
`Statement` and `Evidence`.

### Approval class is decided by reversibility

| Class | When | What you must write in `Mitigation`/`Verification` |
|---|---|---|
| `none` | The old value was `NULL`, a default, or recomputable; nothing wrote to it | Say it is reversible and why |
| `schedule` | Restorable with effort: backfill, long lock, coordinated release | The window, the throttle, the abort condition |
| `accept-risk` | The old value is overwritten or deleted; data is removed with the structure; a narrowing type change | The restore point and the recovery plan |

A `BLOCKER` or `HIGH` finding may never say `Approval: none`. An `Irreversible: no` finding may never
say `Approval: none`.

---

## Evidence: what counts and what does not

`**Evidence:**` is not a summary of what you read. It is a list of citations a reviewer can open.

| Counts as evidence | Does not count |
|---|---|
| `app/controllers/users_controller.rb:31` with the line quoted | "the application still uses the column" |
| `schema.sql:4` — the data fact quoted with its query, count and the line it sits on (`→ 84,302`) | `schema.sql` on its own, with no line and no number |
| `migration.sql:2` — the statement under review | "the migration drops a column" |
| `context.md:17` — deployment fact quoted, with its line | "the deploy is probably rolling" |

**A filename without a line is not evidence.** `see schema.sql` proves the file was named, not that any
part of it was read, and the scorer does not credit it. Every citation needs a line you can open:
`path:line`, `path:12-18`, or `path line 12`.

The report must show, per finding, **which file you read and where in it the claim comes from**. Say
`migration.sql:2` for the statement itself; that is a citation even though the file is short.

### The evidence failure, shown

This is the most common way a correct finding scores zero, and it is the reason the evidence metric
exists. The verdict and the risk are right; the evidence is a summary.

```markdown
### F1 — BLOCKER — destructive-column-drop
- **Statement**: `ALTER TABLE users DROP COLUMN phone` in `migration.sql:2`
- **Evidence**:
  - the application still reads `phone`
  - there are some rows with values
- **Impact**: the profile endpoint breaks after the deploy
```

REJECTED: both evidence bullets are claims, not citations. `"the application still reads phone"` names
no file; `"there are some rows with values"` gives no count and no query. A reviewer cannot open
either, and a scorer cannot distinguish this from a guess. The same finding, evidenced:

```markdown
- **Evidence**:
  - `application-usage.md:9` — `app/controllers/users_controller.rb:31` renders `slice(:id, :email, :phone)`
  - `schema.sql:4` — `phone varchar(32)`; `SELECT count(*) ... WHERE phone IS NOT NULL → 240,000`
```

REJECTED is the first form. The second form is the contract.

If you cannot cite, you do not have a finding. You have an **Unknown** — put it in `Scope → Unknowns`,
state how the missing value changes the risk, and keep the verdict at `CONDITIONAL` when it could hide
a `BLOCKER`.

---

## Lint your own report before you send it

```bash
python scripts/linter_report.py <report.md>
```

It checks the six headings, the verdict token, the `F<n> — SEVERITY — category` shape, all seven
fields, a `path:line` citation per risk finding, the approval class against reversibility, and any
leftover `<placeholder>`. Exit code 1 means the report is not yet conformant; each deviation names the
line and the fix.

---

## Good and bad, side by side

The same top finding, written both ways. Only the left one is parseable.

| Good | Bad |
|---|---|
| `### F1 — BLOCKER — destructive-column-drop` | `### 1. Application still writes phone` |
| `- **Evidence**:` then ``- `app/controllers/users_controller.rb:31` — `slice(:phone)` `` | `The controller still uses it.` |
| `- **Approval**: accept-risk` | *(no approval line at all)* |
| `## Verdict` then `**BLOCKED** — ...` | `## Verdict: ⛔ BLOCK MERGE` |
| six sections, in order | `## Evidence`, `## Blast radius`, `## Summary table` |

A full worked pair — one conforming report, one rejected report, annotated line by line — is in
`examples/good-report.md` and `examples/bad-report.md`.
