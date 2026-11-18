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
    evidence_coverage                required findings whose must_cite_evidence paths appear in report
    destructive_policy_compliance    destructive cases where an approval gate is stated
    verdict_accuracy                 runs whose verdict is an allowed verdict

Exit code 0 when every metric meets its target in docs/evaluation.md, 1 otherwise (unless --report-only).
Stdlib only; no third-party dependencies.
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

APPROVAL_RE = re.compile(
    r"(accept[- ]risk|accept the risk|human approval|requires? approval|approval required|"
    r"sign-?off|approve before|persetujuan)",
    re.IGNORECASE,
)
SCHEDULE_RE = re.compile(r"(schedule|window|maintenance window|jadwal|throttle)", re.IGNORECASE)
REVERSIBLE_NONE_RE = re.compile(r"(approval required\W*:?\s*`?none`?|reversible|no approval)", re.IGNORECASE)

SECTION_PATTERNS = {
    "scope": r"^#{1,3}\s*scope\b",
    "verdict": r"^#{1,3}\s*verdict\b",
    "findings": r"^#{1,3}\s*findings\b",
    "unknowns": r"unknowns?\b",
    "safer_migration_path": r"(safer migration path|migrasi yang lebih aman|recommended migration)",
    "rollback_and_recovery": r"(rollback and recovery|rollback\b)",
    "pre_deploy_checklist": r"(pre-?deploy|checklist)",
}


def _norm(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", text.lower()).strip()


def _normalise_verdict(token: str) -> str | None:
    return VERDICT_ALIASES.get(token.upper())


def parse_report(raw: str) -> Report:
    """Parse the Markdown report contract from SKILL.md.

    Tolerant by design, because a correct analysis written in a slightly different structure must not
    be scored as a miss. Two layouts are accepted:

    1. Canonical: `### F1 — BLOCKER — category`, severity in the heading.
    2. Prose: `## Evidence` with `### 1. Title` subsections, severity in a `**Risk: LOW**` line.

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

    for line in lines:
        hm = heading_re.match(line)
        if hm:
            level = len(hm.group(1))
            title = hm.group(2)
            sev = None
            for s in SEVERITIES:
                if re.search(rf"\b{s}\b", title, re.IGNORECASE):
                    sev = s
                    break
            # Canonical finding heading, or a numbered subsection under an Evidence-like section.
            looks_canonical = sev is not None and re.search(r"\bF\d+\b|\[|—|:", title)
            looks_numbered = bool(re.match(r"^\d+[.)]\s+\S", title))
            if (looks_canonical or looks_numbered) and level >= 2:
                if current is not None:
                    close()
                current = Finding(severity=sev, title=title, body="")
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
        has_approval_gate=bool(APPROVAL_RE.search(raw)),
    )


def _finding_matches(expected: dict, report: Report) -> Finding | None:
    """Find the reported finding that corresponds to an expected finding.

    Match rule: same severity, and either the category matches or at least one
    must_mention_any phrase appears in the finding text. Severity match is required
    because a downgraded severity is a substantive miss, not a wording difference.
    """
    mentions = [_norm(m) for m in expected.get("must_mention_any", [])]

    def concept_match(f: Finding) -> bool:
        if f.category == expected["category"]:
            return True
        text = _norm(f.text)
        return bool(mentions) and any(m and m in text for m in mentions)

    # Pass 1: same severity AND same concept. The strongest, intended match.
    for f in report.findings:
        if f.severity == expected["severity"] and concept_match(f):
            return f

    # Pass 2: the report never attributed a severity to any finding. Credit the concept so recall
    # measures "did it identify this risk", and let format_compliance measure the missing severity.
    # A report that DOES use severities but picks the wrong one is deliberately not rescued here.
    if not any(f.explicit_severity for f in report.findings):
        for f in report.findings:
            if concept_match(f):
                return f
    return None


def _evidence_present(expected: dict, report: Report, case_dir: Path | None = None) -> bool:
    """Did the report cite the evidence this finding requires?

    `must_cite_evidence` names a case file, but a good report cites the *paths documented inside* that
    file (`app/controllers/users_controller.rb:31`), not the fixture filename. Either satisfies the
    requirement: the report must show it read the evidence, and quoting the real path is the stronger
    form.
    """
    raw_lower = report.raw.lower()
    paths = expected.get("must_cite_evidence", [])
    if not paths:
        return True

    def satisfied(entry: str) -> bool:
        name = entry.split(":")[0].lower()
        if name in raw_lower:
            return True
        if case_dir is None:
            return False
        source = case_dir / name
        if not source.is_file():
            return False
        # Collect `some/path.ext` references documented inside the fixture file.
        referenced = re.findall(r"[A-Za-z0-9_./-]+\.(?:rb|py|php|ts|js|sql|md|yml|yaml|json)\b", source.read_text(encoding="utf-8"))
        return any(ref.lower() in raw_lower for ref in referenced if ref.lower() != name)

    return all(satisfied(p) for p in paths)


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
    destructive_ok: bool | None = None
    format_ok: bool = False
    format_notes: list[str] = field(default_factory=list)
    missing: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


REQUIRED_HEADINGS = ("Scope", "Verdict", "Findings")


def check_format(report: Report) -> tuple[bool, list[str]]:
    """Report-contract compliance, independent of analytical quality.

    A correct analysis in the wrong structure must score well on recall and badly here, so the two
    properties stay separable in the results.
    """
    problems: list[str] = []
    for heading in REQUIRED_HEADINGS:
        if not re.search(rf"^#{{1,3}}\s*{heading}\b", report.raw, re.IGNORECASE | re.MULTILINE):
            problems.append(f"missing '## {heading}' heading")
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
    return (not problems), problems


def score_case(expected: dict, report: Report, case_dir: Path | None = None) -> CaseScore:
    cs = CaseScore(
        case_id=expected["case_id"],
        kind=expected["kind"],
        verdict=report.verdict,
        verdict_ok=report.verdict in expected.get("allowed_verdicts", [expected["expected_verdict"]]),
    )
    cs.reported_findings = len([f for f in report.findings if f.severity != "INFO"])
    cs.format_ok, cs.format_notes = check_format(report)

    for exp in expected.get("required_findings", []):
        cs.required_total += 1
        is_critical = bool(exp.get("critical"))
        if is_critical:
            cs.critical_total += 1
        cs.evidence_total += 1
        match = _finding_matches(exp, report)
        if match is None:
            cs.missing.append(f"{exp['id']} {exp['severity']} {exp['category']}")
            continue
        cs.required_found += 1
        if is_critical:
            cs.critical_found += 1
        if _evidence_present(exp, report, case_dir):
            cs.evidence_ok += 1
        else:
            cs.notes.append(f"{exp['id']}: missing evidence for {exp.get('must_cite_evidence')}")
        if exp.get("requires_human_approval") and not report.has_approval_gate:
            cs.notes.append(f"{exp['id']}: approval gate not stated")
        # Approval class is scored by what the report actually says, not by wording alone.
        cls = exp.get("approval")
        if cls == "accept-risk" and not APPROVAL_RE.search(report.raw):
            cs.approval_misses += 1
            cs.notes.append(f"{exp['id']}: expected an 'accept-risk' approval gate")
        elif cls == "schedule" and not (SCHEDULE_RE.search(report.raw) or APPROVAL_RE.search(report.raw)):
            cs.approval_misses += 1
            cs.notes.append(f"{exp['id']}: expected a scheduling/window statement")
        elif cls == "none" and APPROVAL_RE.search(report.raw):
            cs.approval_misses += 1
            cs.notes.append(f"{exp['id']}: expected 'no approval needed' but a gate was stated")

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

    irreversible = expected.get("irreversible", expected.get("destructive"))
    if irreversible:
        cs.destructive_ok = report.has_approval_gate

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


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--reports", required=True, type=Path, help="Directory containing one subdirectory per case ID")
    ap.add_argument("--tests", type=Path, default=Path("tests/migration-guard"))
    ap.add_argument("--json", type=Path, help="Write the full scorecard to this path")
    ap.add_argument("--report-only", action="store_true", help="Exit 0 even when targets are missed")
    args = ap.parse_args(argv)

    cases = load_cases(args.tests)
    if not cases:
        print(f"No expected.json found under {args.tests}", file=sys.stderr)
        return 2

    scores: list[CaseScore] = []
    missing_runs: list[str] = []
    for case_id, expected in cases.items():
        case_dir = args.reports / case_id
        report_paths = find_reports(case_dir) if case_dir.is_dir() else []
        if not report_paths:
            missing_runs.append(case_id)
            continue
        for report_path in report_paths:
            report = parse_report(report_path.read_text(encoding="utf-8"))
            score = score_case(expected, report)
            # Distinguish runs of the same case in the printed table.
            if len(report_paths) > 1:
                score.case_id = f"{case_id}/{report_path.parent.name}"
            scores.append(score)

    metrics = aggregate(scores)
    ok, problems = check_targets(metrics)

    print(f"Scored {len(scores)} run(s); {len(missing_runs)} case(s) without a report.")
    if missing_runs:
        print("Missing reports: " + ", ".join(missing_runs))
    print()
    for s in scores:
        flag = "ok " if s.verdict_ok else "BAD"
        print(f"[{flag}] {s.case_id:<8} verdict={s.verdict!s:<12} "
              f"found={s.required_found}/{s.required_total} critical={s.critical_found}/{s.critical_total} "
              f"evidence={s.evidence_ok}/{s.evidence_total} fp={s.false_positives}")
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

    if args.json:
        args.json.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "metrics": metrics,
            "targets_met": ok,
            "problems": problems,
            "missing_runs": missing_runs,
            "cases": [s.__dict__ for s in scores],
        }
        args.json.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        print(f"\nScorecard written to {args.json}")

    if args.report_only:
        return 0
    return 0 if ok and not missing_runs else 1


if __name__ == "__main__":
    raise SystemExit(main())
