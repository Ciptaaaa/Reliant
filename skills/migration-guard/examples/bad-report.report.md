# Migration Guard Report — CASE-EXAMPLE-001

REJECTED: `line 1` — the title carries a case ID. The contract's H1 is exactly
`# Migration Guard Report`. The case identity belongs in `Scope`.

## Verdict: ⛔ BLOCK MERGE

REJECTED: verdict token sits inside the heading (`## Verdict: ...`) instead of being the bolded first
line of a `## Verdict` section, and `BLOCK MERGE` is not one of `BLOCKED` / `CONDITIONAL` / `APPROVED`.

REJECTED: there is no `## Findings` section. `## Evidence` is not one of the six headings, so a parser
sees only evidence prose and no structured finding at all — every recall metric reads zero even though
the analysis found the risk.

## Evidence

### 1. Application still reads the column

`app/controllers/customers_controller.rb:44` reads `customer.fax` when rendering the invoice
letterhead.

REJECTED: a numbered prose subsection has **no severity and no category**. Without a
`### F<n> — SEVERITY — category` heading this item cannot be attributed as a `BLOCKER`, however
correct the sentence is.

REJECTED: no `**Statement**`, `**Impact**`, `**Mitigation**`, `**Verification**`, `**Approval**`,
`**Reversible**` fields. A reviewer cannot tell whether a human must sign this off, and no tool can
enforce the gate.

### 2. Data is lost

The ~9,400 fax values are deleted.

REJECTED: "data is lost" without the supporting data fact (`schema.sql` shows ~9,400 non-NULL) is a
claim, not evidence.

## Blast radius

| Path | Effect |
|---|---|
| Invoice letterhead | breaks |

REJECTED: `## Blast radius` is not a contract section. This content belongs in a finding's `Impact`.

## Recommended fix

Stop using the column first, then drop it later.

REJECTED: `## Recommended fix` should be `## Safer migration path`, and it must be ordered steps with
the engine syntax, not a single sentence.

## Summary

This migration should not be merged.

REJECTED: `## Summary` is not a section. There is also no `## Rollback and recovery` and no
`## Pre-deploy checklist`, so recovery is undocumented exactly where it matters most.
