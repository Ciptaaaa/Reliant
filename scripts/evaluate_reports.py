#!/usr/bin/env python3
"""Score Migration Guard reports against expected.json fixtures.

Usage:
    python scripts/evaluate_reports.py --reports DIR [--tests DIR] [--json OUT]

Layout expected inside DIR (either naming works):

    DIR/<CASE-ID>/report.md
    DIR/<CASE-ID>/<anything>.md        (the first .md file, report.md preferred)

Computes:
    critical_finding_recall          required findings with critical=true that were found
    required_finding_recall          all required findings that were found
    false_positive_rate              forbidden-category hits / findings reported on negative cases
    evidence_coverage                required findings whose must_cite_evidence paths appear inside that finding
    destructive_policy_compliance    irreversible findings that state an approval gate and a recovery plan
    verdict_accuracy                 runs whose verdict is an allowed verdict
    format_compliance                runs that follow the six-heading contract (see scripts/linter_report.py)

Exit code 0 when every metric meets its target in docs/evaluation.md, 1 otherwise (unless --report-only).
Stdlib only; no third-party dependencies.

Changes in v0.2 of this scorer (2026-11-18)
-------------------------------------------
Iterations 1-2 were scored by a version that measured some things wrongly. Each fix below is
justified in docs/evaluation.md §8. None of them touches tests/migration-guard/expected/.

1. Evidence was credited when the fixture filename appeared *anywhere* in the report. The contract
   asks for the path the evidence lives at, cited *inside the finding*. Evidence is now searched in
   the finding body, and a real `path:line` inside the fixture file counts (that is the stronger
   form, and the one the skill asks for).
2. Destructive policy was scored once per *case*; it is now scored per irreversible *finding*.
3. `APPROVAL_RE` matched "human approval" inside "no human approval needed", so a correct approval
   `none` failed. Negated forms are now excluded.
4. A `schedule` expectation could be satisfied by any approval gate; it now needs a window/backfill
   statement.
5. Format compliance used a three-heading subset that no skill document defined. It now checks the
   six headings the contract actually names, and reports the specific deviation.
6. `score_case` was called without `case_dir`, so `_evidence_present` could never resolve a fixture
   filename to the paths documented inside it.
7. Reports that state severity but never reach the expected level were only half-credited (recall
   skipped them while format compliance ignored the mismatch). Mismatches now always count as misses
   and are visible in the run row as `sev_mismatch`.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

SEVERITIES = ("BLOCKER", "HIGH", "MEDIUM", "LOW", "INFO")
VERDICTS = ("BLOCKED", "CONDITIONAL", "APPROVED")
CATEGORIES = (
    "destructive-column-drop",
    "destructive-table-drop",
    "type-change",
    "not-null-add",
    "unique-constraint",
    "foreign-key",
    "index-build",
    "lock-and-rewrite",
    "backfill",
    "rename",
    "charset-collation",
    "implicit-commit",
    "application-compatibility",
    "data-integrity",
    "operational",
    "no-risk-observed",
)

# Targets from docs/evaluation.md. Used only for the pass/fail summary.
TARGETS = {
    "critical_finding_recall": 1.00,
    "required_finding_recall": 0.90,
    "false_positive_rate": 0.20,  # upper bound
    "evidence_coverage": 1.00,
    "destructive_policy_compliance": 1.00,
    "verdict_accuracy": 0.90,
    "format_compliance": 0.90,
}


@dataclass
class Finding:
    severity: str
    title: str
    body: str
    category: str | None = None
    # True when the report explicitly stated a severity, either in the heading or a "**Risk: X**" line.
    # False means the parser defaulted it; recall can still credit the finding, format compliance cannot.
    explicit_severity: bool = False
    lineno: int = 0  # 1-based line of the heading, for diagnostics

    @property
    def text(self) -> str:
        return f"{self.title}\n{self.body}"


@dataclass
class Report:
    verdict: str | None
    findings: list[Finding] = field(default_factory=list)
    sections: set[str] = field(default_factory=set)
    raw: str = ""
    has_approval_gate: bool = False
    # Deviation strings from the contract check, empty when the report conforms.
    format_problems: list[str] = field(default_factory=list)


# Verdict tokens as actually written by models, normalised to the canonical three.
VERDICT_ALIASES = {
    "BLOCKED": "BLOCKED",
    "BLOCK": "BLOCKED",
    "BLOCKER": "BLOCKED",  # severity name used as a verdict; intent is unambiguous
    "BLOCKING": "BLOCKED",
    "DO NOT MERGE": "BLOCKED",
    "REJECT": "BLOCKED",
    "CONDITIONAL": "CONDITIONAL",
    "CONDITIONALLY APPROVED": "CONDITIONAL",
    "APPROVED": "APPROVED",
    "APPROVE": "APPROVED",
    "SAFE": "APPROVED",
    "LGTM": "APPROVED",
}
VERDICT_RE = re.compile(
    r"\b(" + "|".join(re.escape(k) for k in sorted(VERDICT_ALIASES, key=len, reverse=True)) + r")\b",
    re.IGNORECASE,
)
# A line like "**Risk: LOW**" carries severity outside a finding heading.
RISK_LINE_RE = re.compile(r"\*\*\s*risk\s*:?\s*(BLOCKER|HIGH|MEDIUM|LOW|INFO)\b", re.IGNORECASE)

# An approval phrase, without any negation handling. Negation is decided per clause in
# `_has_approval_gate`, because a look-back window only ever covers the phrasings its author thought of:
# "no human approval needed" was handled, "do not approve before …" was not.
APPROVAL_RE = re.compile(
    r"(?:accept[- ]the[- ]risk|accept[- ]risk|human approval|requires? approval|approval required|"
    r"approv(?:e|al)\s+(?:is\s+)?(?:required|needed)|needs?\s+(?:human\s+)?approval|"
    r"sign-?off|approve before|persetujuan)",
    re.IGNORECASE,
)
SCHEDULE_RE = re.compile(
    r"(schedule|scheduled|window|maintenance|throttl|off-peak|low-traffic|backfill|batch|jadwal)", re.IGNORECASE
)
RECOVERY_RE = re.compile(
    r"(recovery|restore|backup|snapshot|restore point|point-in-time|PITR|rollback plan|pulihkan|cadangan)",
    re.IGNORECASE,
)
REVERSIBLE_NONE_RE = re.compile(r"(approval required\W*:?\s*`?none`?|reversible|no approval)", re.IGNORECASE)
# A citation must carry a line number. `CITATION_RE` accepts a bare filename because it is also used to
# discover references mentioned inside a fixture; evidence acceptance is stricter (see _has_citation).
CITATION_RE = re.compile(
    r"[A-Za-z0-9_.\-/]*[A-Za-z0-9_.\-]+\.(?:rb|py|php|ts|tsx|js|jsx|go|java|kt|cs|sql|md|yml|yaml|json|rake|sh)"
    r"(?::\d+)?"
)
# The form the contract actually asks for: a file path plus a line, optionally a range or a
# "line N" phrasing, so `prices.yml` (a file with no lines to open) does not pass as evidence.
PATH_LINE_RE = re.compile(
    r"[A-Za-z0-9_.\-/]*[A-Za-z0-9_.\-]+\.[A-Za-z0-9]+(?::\d+(?:\s*[-–]\s*\d+)?|\s+line\s+\d+|#L\d+)",
    re.IGNORECASE,
)

SECTION_PATTERNS = {
    "scope": r"^#{1,3}\s*scope\b",
    "verdict": r"^#{1,3}\s*verdict\b",
    "findings": r"^#{1,3}\s*findings\b",
    "unknowns": r"unknowns?\b",
    "safer_migration_path": r"(safer migration path|migrasi yang lebih aman|recommended migration)",
    "rollback_and_recovery": r"(rollback and recovery|rollback\b)",
    "pre_deploy_checklist": r"(pre-?deploy|checklist)",
}


def _has_approval_gate(text: str) -> bool:
    """True when the text asks a human to accept risk or to schedule the step.

    Negation is decided per clause, not by a look-back window. The earlier implementation allowed at
    most 24 characters between the negative word and the phrase, so `"Do not approve before the
    backfill completes"` and `"does not have to accept the risk"` were scored as gates — the exact
    failure the fix was meant to remove, just moved further away. Here each clause is examined as a
    whole: if it contains an approval phrase and any negation token before that phrase, the clause is
    read as *not* requiring approval.

    A clause begins after sentence punctuation, a semicolon, a dash, or a coordinating conjunction, so
    "no backfill is needed; require approval from the DBA" keeps both halves correct.
    """
    for clause in re.split(r"[.;!?\n]|\s(?:but|however|although|yet|while)\s|—|–", text):
        lowered = clause.lower()
        match = APPROVAL_RE.search(clause)
        if not match:
            continue
        before = lowered[: match.start()]
        # A negation anywhere before the phrase in the same clause, or a negative framing of the
        # phrase itself ("no approval", "no need for ... approval").
        if re.search(r"\b(?:no|not|never|without|none|nothing|n't)\b", before):
            continue
        if re.match(r"\s*(?:is\s+)?not\s+(?:required|needed|necessary)", lowered[match.end():]):
            continue
        return True
    return False


def _has_citation(text: str) -> bool:
    """True only when the text cites a file *and a line*.

    A bare filename is not evidence: `see migration.sql` proves the file was named, not that any part
    of it was read. Iterations 1-2 scored as cited on that basis, which is why the old evidence number
    was meaningless. `prices.yml` is a common false positive — it matches a filename pattern but there
    is no line to open — so the line number is required.
    """
    return bool(PATH_LINE_RE.search(text))


def _norm(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", text.lower()).strip()


def _normalise_verdict(token: str) -> str | None:
    return VERDICT_ALIASES.get(token.upper())


def parse_report(raw: str) -> Report:
    """Parse the Markdown report contract from SKILL.md.

    Tolerant by design, because a correct analysis written in a slightly different structure must not
    be scored as a miss. Two layouts are accepted:

    1. Canonical: `### F1 — BLOCKER — category`, severity in the heading.
    2. Prose: a subsection (`### 1. Title` or `### 1 — Title`) whose body carries a `**Risk: LOW**`
       line, or whose heading names a severity.

    Prose subsections did not carry severity in iteration-1/2 by design: the model wrote
    `### 1. Application still writes phone` under `## Evidence`. Treating a subsection *under a
    Findings-like section* as a finding is fair — it is a distinct item with its own body. Treating
    every subsection anywhere as a finding is not, and `## Evidence` subtopics were being parsed as
    independent findings.

    Verdict tokens are normalised (`BLOCK` -> `BLOCKED`, `LGTM` -> `APPROVED`, ...), since the token
    spelling is not what the metric is meant to measure.
    """
    lines = raw.splitlines()

    # --- Verdict -------------------------------------------------------------
    # Prefer the text on the "## Verdict" heading or the first bolded/nearby line after it.
    verdict = None
    for idx, line in enumerate(lines):
        hm = re.match(r"^#{1,4}\s*Verdict\b(.*)$", line, re.IGNORECASE)
        if hm:
            tail = hm.group(1)
            m = VERDICT_RE.search(tail) if tail else None
            if m:
                verdict = _normalise_verdict(m.group(1))
                break
        if re.search(r"^\s*\*\*.*(BLOCKED|CONDITIONAL|APPROVED|BLOCK\b|LGTM|SAFE\b)", line, re.IGNORECASE):
            m = VERDICT_RE.search(line)
            if m:
                verdict = _normalise_verdict(m.group(1))
                break
    if verdict is None:
        # Last resort: any verdict token in the first 30 lines (before the findings body).
        for line in lines[:30]:
            m = VERDICT_RE.search(line)
            if m:
                verdict = _normalise_verdict(m.group(1))
                break

    # --- Findings ------------------------------------------------------------
    findings: list[Finding] = []
    current: Finding | None = None
    current_level = 99
    heading_re = re.compile(r"^(#{1,6})\s*(.*)$")
    # Which subsections count as findings depends on the layout the report chose:
    #   * under a Findings/Evidence-like section, numbered subsections are findings;
    #   * with no such section at all (a common layout: `## Verdict` then `## 1. ...`), numbered
    #     `##` subsections are findings, because that is where the analysis lives.
    # The second rule exists so recall can be measured on reports that ignored the contract. It does
    # not help their format score, which is the metric for following the contract.
    # The test is a HEADING match, not "the word appears anywhere": `## Summary of evidence relied on`
    # is not a findings section, and treating it as one dropped the real findings.
    in_findings = False
    has_findings_section = bool(
        re.search(
            r"^#{1,3}\s*(?:findings?|risks?|evidence|analysis)\s*[:—-]?\s*$",
            raw,
            re.IGNORECASE | re.MULTILINE,
        )
    )
    top_level_numbered = not has_findings_section

    def close() -> None:
        nonlocal current
        if current is not None:
            # Backfill severity from a "**Risk: X**" line when the heading did not carry one.
            if current.severity is None:
                m = RISK_LINE_RE.search(current.body) or RISK_LINE_RE.search(current.title)
                current.severity = m.group(1).upper() if m else "INFO"
            else:
                current.explicit_severity = True
            findings.append(current)
            current = None

    for idx, line in enumerate(lines, start=1):
        hm = heading_re.match(line)
        if hm:
            level = len(hm.group(1))
            title = hm.group(2)
            if level == 2:
                in_findings = bool(re.search(r"\b(findings?|risks?|evidences?|analysis)\b", title, re.IGNORECASE))
            sev = None
            for s in SEVERITIES:
                if re.search(rf"\b{s}\b", title, re.IGNORECASE):
                    sev = s
                    break
            # Canonical finding heading (F<n> + severity), or a prose subsection where analysis lives.
            looks_canonical = sev is not None and re.search(r"\bF\d+\b|\[|—|–", title)
            numbered = bool(re.match(r"^\d+[.)]\s+\S", title))
            looks_prose = numbered and level >= 2 and (in_findings or (top_level_numbered and level == 2))
            if (looks_canonical or looks_prose) and level >= 2:
                if current is not None:
                    close()
                current = Finding(severity=sev, title=title, body="", lineno=idx)
                current_level = level
                continue
            if current is not None and level <= current_level:
                close()
        if current is not None:
            current.body += line + "\n"
    close()

    for f in findings:
        for cat in CATEGORIES:
            if cat in f.text.lower():
                f.category = cat
                break

    sections = set()
    for key, pattern in SECTION_PATTERNS.items():
        if re.search(pattern, raw, re.IGNORECASE | re.MULTILINE):
            sections.add(key)

    return Report(
        verdict=verdict,
        findings=findings,
        sections=sections,
        raw=raw,
        has_approval_gate=_has_approval_gate(raw),
    )


def is_irreversible(expected: dict) -> bool:
    """Does this expected finding describe a step that loses data irreversibly?

    Two spellings exist in the fixtures: `reversible: false` and `approval: accept-risk`. Both are
    accepted so a fixture that states the approval class without the reversibility flag still counts.
    See `skills/migration-guard/references/change-impact-taxonomy.md` §2.4.
    """
    return expected.get("reversible") is False or expected.get("approval") == "accept-risk"


def _finding_matches(expected: dict, report: Report, used: set[int] | None = None) -> Finding | None:
    """Find the reported finding that corresponds to an expected finding.

    Match rule: same severity, and either the category matches or at least one
    must_mention_any phrase appears in the finding text. Severity match is required
    because a downgraded severity is a substantive miss, not a wording difference.

    `used` holds ids of findings already matched to another expectation. A case may legitimately have
    two findings with the same severity and category (PG-006 and MY-006 both do), so without this the
    evaluator returned the same report finding for both and credited its evidence twice while the
    second expectation was marked as missing evidence. First match wins, in document order.
    """
    mentions = [_norm(m) for m in expected.get("must_mention_any", [])]
    used = used if used is not None else set()

    def phrase_match(f: Finding) -> bool:
        text = _norm(f.text)
        return bool(mentions) and any(m and m in text for m in mentions)

    def take(f: Finding) -> Finding:
        used.add(id(f))
        return f

    # Pass 1: same severity AND same category. A category hit is stronger evidence of identity than a
    # phrase hit, so it must be tried before phrases; otherwise a generic phrase like "lock" lets an
    # application-compatibility expectation swallow a lock-and-rewrite finding (PG-002).
    for f in report.findings:
        if id(f) not in used and f.severity == expected["severity"] and f.category == expected["category"]:
            return take(f)

    # Pass 2: same severity AND a distinctive phrase. Phrases are the fallback for a report that used a
    # different category word for the same risk.
    for f in report.findings:
        if id(f) not in used and f.severity == expected["severity"] and phrase_match(f):
            return take(f)

    # Pass 3: the report never attributed a severity to any finding. Credit the concept so recall
    # measures "did it identify this risk", and let format_compliance measure the missing severity.
    # A report that DOES use severities but picks the wrong one is deliberately not rescued here.
    if not any(f.explicit_severity for f in report.findings):
        for f in report.findings:
            if id(f) not in used and (f.category == expected["category"] or phrase_match(f)):
                return take(f)
    return None


def _concept_only_matches(expected: dict, report: Report) -> list[Finding]:
    """Findings that discuss the expected concept but at the wrong severity or category.

    Used only for diagnostics. A concept with the wrong severity is a miss — severity is part of the
    claim — but "reported as MEDIUM instead of HIGH" and "never mentioned" are different defects and
    the result log must be able to tell them apart.
    """
    mentions = [_norm(m) for m in expected.get("must_mention_any", [])]
    hits = []
    for f in report.findings:
        text = _norm(f.text)
        if f.category == expected["category"] or (mentions and any(m and m in text for m in mentions)):
            hits.append(f)
    return hits


def _evidence_present(expected: dict, finding: Finding, case_dir: Path | None = None) -> tuple[bool, str]:
    """Did *this finding* cite the evidence the expectation requires?

    `must_cite_evidence` names a case file. The contract asks the report to cite the path the evidence
    lives at (`app/controllers/users_controller.rb:31`), which is the stronger form — the fixture
    filename alone only proves the file was named, not that it was read. Both are accepted, but the
    search is scoped to the finding body: an unrelated citation elsewhere in the report is not
    evidence for this finding.

    Returns (satisfied, reason) so a miss says what was looked for.
    """
    paths = expected.get("must_cite_evidence", [])
    if not paths:
        return True, ""
    body = finding.text
    missing: list[str] = []

    def satisfied(entry: str) -> bool:
        name = entry.split(":")[0].lower()
        if name in body.lower() and _has_citation(body):
            return True
        if case_dir is None:
            return False
        source = case_dir / name
        if not source.is_file():
            return False
        # Collect `some/path.ext` references documented inside the fixture file.
        referenced = re.findall(
            r"[A-Za-z0-9_./-]+\.(?:rb|py|php|ts|tsx|js|jsx|go|java|kt|cs|sql|md|yml|yaml|json|rake|sh)\b",
            source.read_text(encoding="utf-8"),
        )
        return any(ref.lower() in body.lower() for ref in referenced if ref.lower() != name)

    for p in paths:
        if not satisfied(p):
            missing.append(p)
    return (not missing), ("" if not missing else "missing evidence for " + ", ".join(missing))


@dataclass
class CaseScore:
    case_id: str
    kind: str
    verdict: str | None
    verdict_ok: bool
    required_total: int = 0
    required_found: int = 0
    critical_total: int = 0
    critical_found: int = 0
    evidence_ok: int = 0
    evidence_total: int = 0
    false_positives: int = 0
    reported_findings: int = 0
    approval_misses: int = 0
    destructive_total: int = 0
    destructive_ok_count: int = 0
    destructive_ok: bool | None = None
    format_ok: bool = False
    format_notes: list[str] = field(default_factory=list)
    missing: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


# The six sections SKILL.md names, in order. Kept in sync with scripts/linter_report.py, which is the
# authoritative checker for one report.
REQUIRED_HEADINGS = (
    "Scope",
    "Verdict",
    "Findings",
    "Safer migration path",
    "Rollback and recovery",
    "Pre-deploy checklist",
)


def check_format(report: Report) -> tuple[bool, list[str]]:
    """Report-contract compliance, independent of analytical quality.

    A correct analysis in the wrong structure must score well on recall and badly here, so the two
    properties stay separable in the results.

    The contract is the six headings in SKILL.md, checked positively and negatively: a missing
    heading and an invented heading (a `## Summary` section) are both deviations, because a parser
    keyed on the six names cannot tell them apart from the absence of a section.
    """
    problems: list[str] = []
    headings = re.findall(r"^##\s+(.+?)\s*$", report.raw, re.MULTILINE)
    normalised = [h.strip() for h in headings]
    lowered = [h.lower() for h in normalised]

    for heading in REQUIRED_HEADINGS:
        if heading.lower() not in lowered:
            problems.append(f"missing '## {heading}' heading")
    for h in normalised:
        if h.lower() not in [x.lower() for x in REQUIRED_HEADINGS]:
            problems.append(f"unexpected section '## {h}'")
    present_order = [h for h in lowered if h in [x.lower() for x in REQUIRED_HEADINGS]]
    contract_order = [h.lower() for h in REQUIRED_HEADINGS]
    # Only compare the headings that are present: a missing heading is already reported above, and
    # comparing a filtered list with a filtered list of itself is always equal, so the old check
    # could never fail on order and could never pass either (it compared the wrong pair).
    if present_order != [h for h in contract_order if h in present_order]:
        problems.append(f"sections out of contract order: {' > '.join(present_order)}")

    if report.verdict is None:
        problems.append("no parseable verdict")
    if not report.findings:
        problems.append("no findings parsed")
    else:
        attributed = [f for f in report.findings if f.explicit_severity]
        if not attributed:
            problems.append("no finding carries an explicit severity")
        if not any(f.category for f in report.findings):
            problems.append("no finding carries a known category")
        # A finding heading that does not carry severity+category cannot be parsed deterministically.
        loose = [f for f in report.findings if not f.explicit_severity or not f.category]
        if loose:
            problems.append(f"{len(loose)} finding heading(s) lack an explicit severity or category")
    return (not problems), problems


def _approval_ok(expected_class: str | None, report: Report, finding_text: str, is_irreversible: bool) -> tuple[bool, str]:
    """Did the report state the approval class this finding requires?

    Scored against the finding when the finding carries an approval statement, otherwise against the
    report (older reports discuss approval in prose outside the finding). A miss says which check
    failed, so the number can be audited rather than trusted.
    """
    if expected_class is None:
        return True, ""
    scope = finding_text if re.search(r"\*\*\s*approval\b", finding_text, re.IGNORECASE) else report.raw
    gate = _has_approval_gate(scope)
    if expected_class == "accept-risk":
        if not gate:
            return False, "expected an 'accept-risk' approval gate"
        if is_irreversible and not RECOVERY_RE.search(scope):
            return False, "accept-risk stated without a recovery/backup plan"
        return True, ""
    if expected_class == "schedule":
        if not SCHEDULE_RE.search(scope):
            return False, "expected a schedule/window/backfill statement"
        return True, ""
    # none: the report must NOT ask for a gate. Saying "no approval needed" is correct, not a gate.
    if gate:
        return False, "expected 'no approval needed' but a gate was stated"
    return True, ""


def score_case(expected: dict, report: Report, case_dir: Path | None = None) -> CaseScore:
    cs = CaseScore(
        case_id=expected["case_id"],
        kind=expected["kind"],
        verdict=report.verdict,
        verdict_ok=report.verdict in expected.get("allowed_verdicts", [expected["expected_verdict"]]),
    )
    cs.reported_findings = len([f for f in report.findings if f.severity != "INFO"])
    cs.format_ok, cs.format_notes = check_format(report)

    # Findings already matched to another expectation in this case. Two expectations can share a
    # severity and category, so a report finding must be claimed once.
    used_findings: set[int] = set()

    # The destructive-policy denominator comes from the FIXTURE, not from what the report chose to
    # report. Deriving it from matched findings let a report raise its score by deleting the finding
    # whose gate it got wrong: on PG-001, "F2 present with Approval: none" scored 1/2 (fail) while the
    # same report with F2 deleted scored 1/1 (pass). A miss must count as a miss.
    irreversible_expected = {
        exp["id"]
        for exp in expected.get("required_findings", [])
        if is_irreversible(exp)
    }
    cs.destructive_total = len(irreversible_expected)

    for exp in expected.get("required_findings", []):
        cs.required_total += 1
        is_critical = bool(exp.get("critical"))
        if is_critical:
            cs.critical_total += 1
        cs.evidence_total += 1
        match = _finding_matches(exp, report, used_findings)
        if match is None:
            cs.missing.append(f"{exp['id']} {exp['severity']} {exp['category']}")
            # Name the near miss, so a severity/category mismatch is not mistaken for "never saw it".
            concept = _concept_only_matches(exp, report)
            if concept:
                cs.notes.append(
                    f"{exp['id']}: concept found but severity/category wrong "
                    f"(reported {concept[0].severity}, expected {exp['severity']}; "
                    f"category {concept[0].category or 'unknown'}, expected {exp['category']})"
                )
            continue
        cs.required_found += 1
        if is_critical:
            cs.critical_found += 1
        ok, why = _evidence_present(exp, match, case_dir)
        if ok:
            cs.evidence_ok += 1
        else:
            cs.notes.append(f"{exp['id']}: {why}")

        if exp.get("requires_human_approval") and not _has_approval_gate(report.raw):
            cs.notes.append(f"{exp['id']}: approval gate not stated")

        irreversible = is_irreversible(exp)
        ok, why = _approval_ok(exp.get("approval"), report, match.text, irreversible)
        if not ok:
            cs.approval_misses += 1
            cs.notes.append(f"{exp['id']}: {why}")
        if irreversible and ok:
            # The denominator was already set from the fixture above, so a finding that was never
            # reported simply contributes nothing to the numerator and the case fails.
            if _has_approval_gate(match.text if re.search(r"\*\*\s*approval\b", match.text, re.I) else report.raw):
                cs.destructive_ok_count += 1

    for forb in expected.get("forbidden_findings", []):
        cat = forb["category"]
        limit = forb.get("max_severity_if_present")
        # SEVERITIES runs worst-first, so "at most `limit`" means an index >= the limit's index.
        limit_idx = SEVERITIES.index(limit) if limit else None
        for f in report.findings:
            if f.severity == "INFO":
                continue
            if cat in f.text.lower():
                # No limit means the category must not appear at all.
                if limit_idx is not None and SEVERITIES.index(f.severity) >= limit_idx:
                    continue
                cs.false_positives += 1

    irreversible_case = expected.get("irreversible", expected.get("destructive"))
    if irreversible_case:
        # A case passes only when every irreversible finding carries its gate. The denominator is the
        # fixture's count of irreversible findings, so an unreported one is a guaranteed miss; the
        # report cannot improve its ratio by staying quiet.
        cs.destructive_ok = (
            cs.destructive_total > 0
            and cs.destructive_ok_count == cs.destructive_total
        )

    return cs


def find_reports(case_dir: Path) -> list[Path]:
    """Return every report for a case.

    Supports both layouts:
      <case>/report.md              single run
      <case>/run-N/report.md        one report per run (protocol asks for 3)
    """
    direct = case_dir / "report.md"
    if direct.is_file():
        return [direct]
    runs = sorted(case_dir.glob("run-*/report.md"))
    if runs:
        return runs
    loose = sorted(case_dir.glob("*.md"))
    return loose[:1]


def aggregate(scores: list[CaseScore]) -> dict:
    def ratio(num: int, den: int) -> float | None:
        return None if den == 0 else round(num / den, 4)

    required_total = sum(s.required_total for s in scores)
    required_found = sum(s.required_found for s in scores)
    critical_total = sum(s.critical_total for s in scores)
    critical_found = sum(s.critical_found for s in scores)
    evidence_total = sum(s.evidence_total for s in scores)
    evidence_ok = sum(s.evidence_ok for s in scores)

    neg = [s for s in scores if s.kind == "negative"]
    neg_findings = sum(s.reported_findings for s in neg)
    neg_fp = sum(s.false_positives for s in neg)
    all_fp = sum(s.false_positives for s in scores)
    all_findings = sum(s.reported_findings for s in scores)

    destructive = [s for s in scores if s.destructive_ok is not None]
    destructive_ok = sum(1 for s in destructive if s.destructive_ok)

    metrics = {
        "critical_finding_recall": ratio(critical_found, critical_total),
        "required_finding_recall": ratio(required_found, required_total),
        "false_positive_rate": ratio(neg_fp, neg_findings) if neg_findings else (0.0 if neg_fp == 0 else None),
        "false_positive_categories": all_fp,
        "evidence_coverage": ratio(evidence_ok, evidence_total),
        "destructive_policy_compliance": ratio(destructive_ok, len(destructive)),
        "verdict_accuracy": ratio(sum(1 for s in scores if s.verdict_ok), len(scores)),
        "format_compliance": ratio(sum(1 for s in scores if s.format_ok), len(scores)),
        "cases": len(scores),
        "reported_findings": all_findings,
    }
    return metrics


def check_targets(metrics: dict) -> tuple[bool, list[str]]:
    problems = []
    for name, target in TARGETS.items():
        value = metrics.get(name)
        if value is None:
            problems.append(f"{name}: no data (target {target})")
            continue
        ok = value <= target if name == "false_positive_rate" else value >= target
        if not ok:
            problems.append(f"{name}: {value} vs target {target}")
    return not problems, problems


def load_cases(tests_dir: Path) -> dict[str, dict]:
    cases: dict[str, dict] = {}
    for p in sorted(tests_dir.glob("expected/**/expected.json")):
        data = json.loads(p.read_text(encoding="utf-8"))
        cases[data["case_id"]] = data
    return cases


def _safe_console() -> None:
    """Never crash on a model's Unicode.

    Reports routinely contain emoji and typographic dashes. On a Windows console with a legacy code
    page, printing one raises `UnicodeEncodeError` and the whole scorecard is lost — the scoring
    itself was fine, only the summary crashed. Reconfigure stdout/stderr to UTF-8 with replacement so a
    character that cannot be shown prints as `?` instead of killing the run.
    """
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]
        except (AttributeError, ValueError):
            pass


def main(argv: list[str] | None = None) -> int:
    _safe_console()
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--reports", required=True, type=Path, help="Directory containing one subdirectory per case ID")
    ap.add_argument("--tests", type=Path, default=Path("tests/migration-guard"))
    ap.add_argument("--json", type=Path, help="Write the full scorecard to this path")
    ap.add_argument("--report-only", action="store_true", help="Exit 0 even when targets are missed")
    ap.add_argument("--compare", type=Path, action="append", metavar="SCORECARD",
                    help="Compare against a previously written scorecard JSON (repeatable)")
    ap.add_argument("--quiet-cases", action="store_true", help="Print only the metric table, not per-run rows")
    args = ap.parse_args(argv)

    cases = load_cases(args.tests)
    if not cases:
        print(f"No expected.json found under {args.tests}", file=sys.stderr)
        return 2

    scores: list[CaseScore] = []
    missing_runs: list[str] = []
    for case_id, expected in cases.items():
        case_dir = args.tests / "cases"
        case_dir = next((p.parent for p in case_dir.glob(f"*/{case_id}")), None) or (args.tests / "cases" / case_id)
        report_root = args.reports / case_id
        report_paths = find_reports(report_root) if report_root.is_dir() else []
        if not report_paths:
            missing_runs.append(case_id)
            continue
        for report_path in report_paths:
            report = parse_report(report_path.read_text(encoding="utf-8", errors="replace"))
            # case_dir was never passed before v0.2, so evidence inside a fixture file could not be
            # resolved. Passing it is what makes `must_cite_evidence` checkable at all.
            score = score_case(expected, report, case_dir)
            if len(report_paths) > 1:
                score.case_id = f"{case_id}/{report_path.parent.name}"
            scores.append(score)

    metrics = aggregate(scores)
    ok, problems = check_targets(metrics)

    print(f"Scored {len(scores)} run(s); {len(missing_runs)} case(s) without a report.")
    if missing_runs:
        print("Missing reports: " + ", ".join(missing_runs))
    print()
    if not args.quiet_cases:
        for s in scores:
            flag = "ok " if s.verdict_ok else "BAD"
            print(f"[{flag}] {s.case_id:<18} verdict={str(s.verdict):<12} fmt={'y' if s.format_ok else 'n'} "
                  f"found={s.required_found}/{s.required_total} critical={s.critical_found}/{s.critical_total} "
                  f"evidence={s.evidence_ok}/{s.evidence_total} ap_miss={s.approval_misses} fp={s.false_positives}")
            for m in s.missing:
                print(f"        MISSED: {m}")
            for n in s.notes:
                print(f"        NOTE:   {n}")
        print()
    for k, v in metrics.items():
        print(f"{k:<32} {v}")
    print()
    if problems:
        print("Below target:")
        for p in problems:
            print(f"  - {p}")
    else:
        print("All metrics meet their targets.")

    if args.compare:
        print()
        print("Comparison (value, then delta against each scorecard):")
        previous = []
        for path in args.compare:
            try:
                previous.append((path, json.loads(path.read_text(encoding="utf-8")).get("metrics", {})))
            except (OSError, json.JSONDecodeError) as exc:
                print(f"  ! cannot read {path}: {exc}")
        header = f"  {'metric':<32} {'now':>8}" + "".join(f" {p.name[:18]:>20}" for p, _ in previous)
        print(header)
        for name in TARGETS:
            now = metrics.get(name)
            cells = []
            for _, prev in previous:
                before = prev.get(name)
                if now is None or before is None:
                    cells.append(f"{'—':>20}")
                else:
                    better = (now <= before) if name == "false_positive_rate" else (now >= before)
                    arrow = "up" if now > before else ("down" if now < before else "same")
                    mark = "better" if better and now != before else ("worse" if not better and now != before else "")
                    cells.append(f"{arrow:>5} {now - before:+.4f} {mark:>6}")
            print(f"  {name:<32} {now if now is not None else '—'!s:>8}" + "".join(cells))
        print("  (target: " + ", ".join(f"{k}<={v}" if k == "false_positive_rate" else f"{k}>={v}"
                                        for k, v in TARGETS.items()) + ")")

    if args.json:
        args.json.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "metrics": metrics,
            "targets_met": ok,
            "problems": problems,
            "missing_runs": missing_runs,
            "scorer_version": "0.2",
            "cases": [s.__dict__ for s in scores],
        }
        args.json.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        print(f"\nScorecard written to {args.json}")

    if args.report_only:
        return 0
    return 0 if ok and not missing_runs else 1


if __name__ == "__main__":
    raise SystemExit(main())
