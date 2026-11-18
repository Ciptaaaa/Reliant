---
name: migration-guard
description: Evidence-based risk analysis for database migrations. Use when a change adds, alters, or drops a table, column, index, or constraint in PostgreSQL or MySQL; when writing or reviewing ALTER TABLE, CREATE INDEX, or data backfill scripts; or when asked whether a migration is safe to deploy, needs a rollback plan, or will cause locking or downtime.
license: MIT
compatibility: Designed for coding agents that can read repository files. Requires no database connection. PostgreSQL and MySQL only.
metadata:
  author: reliant
  version: "0.1.0"
  phase: "1"
---

# Migration Guard

Analyze a database migration **before** it is considered done. The output is a risk report backed
by evidence, plus a safer migration path. You do not execute migrations.

## What makes this different from a normal review

A migration that is syntactically valid can still destroy data, block a table for minutes, or break
the running application version. Your job is to find out whether this specific migration, against
this specific schema and this specific application code, is safe — and to prove it.

**The rule you must never break:** every risk level you assign must cite evidence you actually
found. If you lack evidence, downgrade your confidence and say what you could not verify — do not
guess and do not stay silent about the gap.

## Hard constraints

- Do not run the migration, and do not connect to a database. Read files only.
- Do not request production credentials, and do not suggest that the user paste production data.
- Never produce an `APPROVED` verdict when any evidence needed for the riskiest finding is missing.
  Use `CONDITIONAL` with an explicit verification step instead.
- A finding may never be more severe than the evidence supports. Unverified data property → `HIGH`
  with the check query, not `BLOCKER`. See [risk classification](references/risk-classification.md) §2.
- Any data change needs whatever permission the host agent requires. On top of that, an
  **approval to accept risk** is required only when the old value cannot be restored. Reversibility,
  not the operation type, decides — see "Approvals" below.
- Do not invent table sizes, row counts, lock durations, or database behavior. If the user did not
  provide them, list them under **Unknowns** and state how a missing value changes the risk.

## Workflow

Work through these steps in order. Do not skip steps, and do not write the report until you finish
step 5.

- [ ] **Step 1 — Identify engine and version.** Find the engine (PostgreSQL or MySQL) and the version
      from config, Dockerfiles, CI, `package.json`, `requirements.txt`, `Gemfile`, or the user's
      message. Version changes the answer for many operations. If unknown, say so and record it in
      **Unknowns** — it may cap the verdict at `CONDITIONAL`. If the engine is MariaDB or anything
      other than PostgreSQL/MySQL, say the analysis does not apply and stop at `CONDITIONAL`.
- [ ] **Step 2 — Reconstruct the schema.** Read migrations, `schema.sql`, or an ORM model to learn
      the current definitions of every table the migration touches: columns, types, nullability,
      defaults, indexes, constraints, foreign keys, and approximate size if available.
- [ ] **Step 3 — Read the migration.** Parse every statement. For each one, record exactly what it
      does to the schema, and classify it as additive, modifying, or destructive.
- [ ] **Step 4 — Trace application usage.** Search the codebase for every affected identifier (table,
      column, index, constraint). Include raw SQL, ORM models, query builders, views, migrations
      still pending, seeders, analytics jobs, reporting code, and ETL scripts. Cite `path:line` for
      each hit, and note absence explicitly (`no references outside schema.sql`).

      **How to get the line number, because a citation without one does not count.** Use
      `grep -rn "<identifier>" app/ lib/ config/` and paste its output verbatim: it is already
      `path:line:text`. Do not renumber by hand and do not cite a file you only skimmed. The
      statement under review is `migration.sql:<n>` from the same grep. Every bullet under
      **Evidence** needs a line, including the data facts from `schema.sql` (`schema.sql:4`).

      A claim you cannot turn into `path:line` is not a finding. Move it to `Scope → Unknowns` and say
      what the missing value would change.
- [ ] **Step 5 — Assess operational impact and rollback.** Decide whether the change rewrites the
      table, takes a blocking lock, needs a backfill, or is irreversible. Compare against the
      deployment model: does an older application version still run while the new schema is live?
      Then classify each finding and compute the verdict.

Steps 1-3 usually need `references/risk-classification.md` only. Load engine specifics only when the
migration needs them:

- PostgreSQL (locks, `CONCURRENTLY`, `NOT VALID`, rewrites, `USING`):
  read `references/postgres.md`.
- MySQL/InnoDB (`ALGORITHM=INSTANT|INPLACE|COPY`, `LOCK=`, metadata locks, implicit commit):
  read `references/mysql.md`.
- Need a safer version of a risky operation: read `references/safe-migration-patterns.md`.
- Need a recovery or downgrade plan, or the user asked about rollback: read
  `references/rollback-strategy.md`.

- [ ] **Step 6 — Write the report from the template, then lint it.** Open `report-template.md` and copy
      its skeleton. It contains the only accepted structure. Then run the linter on your own output:

      ```bash
      python scripts/linter_report.py <the report you just wrote>
      ```

      Do not send a report that fails the linter. The linter and the scorer read the same contract, so
      a non-conforming report is scored as a miss even when the analysis is right.

## Gotchas

These are the mistakes that make a migration review wrong:

- **A valid migration is not a safe migration.** `ALTER TABLE users DROP COLUMN phone;` parses fine
  and still breaks every code path that selects `phone`. Never treat "the SQL is valid" as a verdict.
- **"No references found" is weak evidence.** The column may be used in a reporting repo, a BI
  dashboard, a mobile app, or a migration not yet applied. Say where you looked, and treat
  unsearched surfaces as unknowns.
- **The old application version is still running during a rolling deploy.** Renames, drops, and
  `NOT NULL` additions break version N while version N+1 is still rolling out.
- **MySQL DDL implicitly commits.** A "transaction-wrapped" DDL is not atomic. A failed multi-statement
  migration may leave half the changes applied.
- **PostgreSQL `CREATE INDEX CONCURRENTLY` cannot run inside a transaction block**, and a failed
  concurrent build leaves an `INVALID` index behind.
- **Adding `NOT NULL` without a default requires a full table scan and fails on existing `NULL`s**
  unless you use the add-constraint-as-`NOT VALID` then `VALIDATE` pattern.
- **`MEDIUM` is not a pass.** A backfill on a large unspecified table is operational risk, not a
  formality.

## Risk levels

Use exactly these five levels. Full criteria live in `references/risk-classification.md`.

| Level | Meaning |
|---|---|
| `BLOCKER` | Unsafe based on evidence (proven data loss or application breakage in scope) |
| `HIGH` | Large risk to data or operations (likely data loss, rewrite, blocking lock, deploy breakage) |
| `MEDIUM` | Risk that requires mitigation (backfill, coordinated release, extra verification) |
| `LOW` | Relatively small risk (additive, non-destructive, cheap on the stated schema) |
| `INFO` | Additional information; no risky finding in the inspected scope |

## Verdict

| Verdict | When |
|---|---|
| `BLOCKED` | At least one unresolved `BLOCKER` finding |
| `CONDITIONAL` | No `BLOCKER`, but at least one `HIGH` or `MEDIUM` finding, or a material unknown |
| `APPROVED` | No finding above `LOW`, all required evidence present, scope stated |

`APPROVED` means only that no blocker was found in the inspected scope with the evidence available.
Say this in the report every time. It is not a guarantee for production.

Report the verdict as the single worst case:

1. Any `BLOCKER` → `BLOCKED`.
2. Else any `HIGH`/`MEDIUM`, or any unknown that could hide a `BLOCKER` (e.g. row count unknown for
   a `NOT NULL` backfill, old application version status unknown for a rename) → `CONDITIONAL`.
3. Else `APPROVED`.

## Approvals

Separate two questions that are easy to merge:

- **Approval to execute** — the host agent's own permission model decides this. The skill cannot grant
  it and does not replace it. Every write, and every step that would touch a database, needs it.
- **Approval to accept risk** — a human decision that data loss or irreversible change is acceptable.
  Required only when the previous value **cannot be restored**.

Reversibility decides the class, not the operation name. An `UPDATE` can be either class; a `DROP
COLUMN` never makes it reversible.

| Class | Test | Required |
|---|---|---|
| Reversible, additive | Old value was `NULL` or default; data is derived and recomputable; column/table added and nothing wrote to it | `none` — state that it is reversible and why |
| Reversible but costly | Restorable with effort/schedule: large backfill, coordinated release, long lock | `schedule` — window, throttle, abort conditions |
| Irreversible | Old value overwritten or deleted; structure removed with data; narrowing type change | `accept-risk` — plus recovery plan and restore point |

If an irreversible step has no recovery plan, the finding is at least `BLOCKER`. A reversible step
without a recovery plan is not.

## Report format

The report is **machine-parsed**. There is exactly one accepted structure, and it lives in
[`report-template.md`](report-template.md). Open that file, copy its skeleton into your answer, and
replace every `<...>`. Do not restyle it, do not add a heading, do not rename a heading.

The six headings, in this order — nothing else:

```text
# Migration Guard Report
## Scope
## Verdict
## Findings
## Safer migration path
## Rollback and recovery
## Pre-deploy checklist
```

`## Evidence`, `## Blast radius`, `## Summary`, `## Conclusion`, `## Notes`, `## Recommended fix` are all
**not** sections. Their content belongs inside a finding (`Impact`, `Evidence`) or in one of the six.
A report with seven sections is unparseable even when every sentence in it is correct.

Every finding is exactly this shape:

```markdown
### F1 — BLOCKER — destructive-column-drop
- **Statement**: `ALTER TABLE users DROP COLUMN phone` in `migration.sql:2` — removes the column and its values
- **Evidence**:
  - `app/controllers/users_controller.rb:31` — `slice(:phone)` returns it in the API response
  - `migration.sql:2` — the `DROP COLUMN` under review
  - `schema.sql:4` — `phone varchar(32)`; ~240,000 non-NULL values, so this removes live data
- **Impact**: the profile endpoint raises `PG::UndefinedColumn`; the 240,000 values are gone
- **Mitigation**: retire every reader and deploy first; drop in a later, separately approved migration
- **Verification**: `grep -rn "phone" app/ lib/` finds no reader; the nightly export succeeds twice
- **Approval**: accept-risk
- **Reversible**: no
```

- `SEVERITY` is one of `BLOCKER`, `HIGH`, `MEDIUM`, `LOW`, `INFO` — the literal token, in the heading.
- `category` is one of the sixteen in [`report-template.md`](report-template.md#categories-use-exactly-one-per-finding).
- `**Approval**` must end with exactly `none`, `schedule`, or `accept-risk`.
- `**Reversible**` must be `yes` or `no`.
- A clean migration gets one `### F1 — INFO — no-risk-observed` finding. A report with no findings is a
  failure, not a pass.
- A lock, rewrite, backfill, or replica-lag cost is **its own finding** with `Approval: schedule`. Do not
  leave it only in `## Safer migration path` or the checklist — a cost that is not a finding is a cost
  nobody gates.
- Two findings may share a severity and category; they are different mechanisms and each needs its own
  `Statement` and `Evidence`.

**Before you answer, lint the report you wrote:**

```bash
python scripts/linter_report.py <report.md>
```

Exit code 1 lists each deviation with its line and the fix. Send nothing that fails. The scorer and the
linter read the same contract, so a non-conforming report loses recall credit even when the analysis is
right — that is the defect that made earlier iterations score 0% on format.

## Calibration examples

**Destructive and used → BLOCKER.**

```sql
ALTER TABLE users DROP COLUMN phone;
```

Evidence: `app/api/users.py:41` selects `phone`; `app/models/user.rb:12` declares the attribute;
scope note says the column is also exported to a partner integration.

**Edge case that is easy to get wrong: `NOT NULL` on an empty-in-practice column.**

```sql
ALTER TABLE orders ADD CONSTRAINT orders_status_not_null CHECK (status IS NOT NULL) NOT VALID;
ALTER TABLE orders VALIDATE CONSTRAINT orders_status_not_null;
```

If `application-usage.md` and the user confirm there are zero NULL rows and the table is small, this
is `LOW`. If NULL rows exist, `VALIDATE` fails and the migration is `HIGH` with a backfill-then-validate
mitigation. Do not apply the PostgreSQL pattern blindly in MySQL — the mechanisms differ.

A full worked pair — one conforming report and one rejected report on the same migration, annotated
line by line — is in `examples/good-report.report.md` and `examples/bad-report.report.md`.

## Anti-patterns to reject

- A report with zero findings that never states what was inspected.
- A severity without a citation.
- "Looks safe" or "should be fine" without a rollback and verification plan.
- Treating a pending backfill "to be done later" as a mitigation. Either it is scheduled and
  specified, or the finding stands.
- Recommending `IF EXISTS` or `IF NOT EXISTS` as a safety measure. It hides a mismatch between the
  expected schema and the real one.
