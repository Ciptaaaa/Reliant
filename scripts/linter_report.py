#!/usr/bin/env python3
"""Lint a Migration Guard report against the report contract in SKILL.md.

Why this exists
---------------
Iterations 1 and 2 showed 0% format compliance across 60 runs. The diagnosis in docs/evaluation.md
was that "a format that can be ignored 60 times out of 60 is not a contract". That is true, but it is
only half the story: the contract was also *unenforceable*. A prose instruction cannot be checked by
the agent while it writes, so a near-miss and a total miss scored identically.

This linter turns the contract into a checklist the agent can run on itself, and a reviewer can run on
a report. It reports every deviation with a line number and the exact fix, in the order the agent
should apply them.

Usage:
    python scripts/linter_report.py evals/results/iteration-3/PG-001/run-1/report.md
    python scripts/linter_report.py --reports evals/results/iteration-3        (every report below)
    python scripts/linter_report.py -                                   (read stdin)

Exit code 0 when the report conforms, 1 when it does not. Stdlib only.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

# The six headings, in the order the contract requires. Matching is exact: a report that renames
# "## Safer migration path" to "## Recommended path" is the deviation this linter exists to catch.
REQUIRED_HEADINGS = (
    "Scope",
    "Verdict",
    "Findings",
    "Safer migration path",
    "Rollback and recovery",
    "Pre-deploy checklist",
)

REQUIRED_FINDING_FIELDS = (
    "Statement",
    "Evidence",
    "Impact",
    "Mitigation",
    "Verification",
    "Approval",
    "Reversible",
)

SEVERITIES = ("BLOCKER", "HIGH", "MEDIUM", "LOW", "INFO")
VERDICTS = ("BLOCKED", "CONDITIONAL", "APPROVED")
APPROVAL_CLASSES = ("none", "schedule", "accept-risk")
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

FINDING_HEADING_RE = re.compile(
    r"^###\s+F(?P<num>\d+)\s+[—–-]\s+(?P<sev>BLOCKER|HIGH|MEDIUM|LOW|INFO)\s+[—–-]\s+(?P<cat>[a-z][a-z0-9-]*)\s*$"
)
# Any other `###` inside Findings is a structural deviation worth reporting explicitly.
LOOSE_FINDING_RE = re.compile(r"^###\s+(?P<title>.+)$")
# A path citation: at least one slash or a known source extension, optional line number.
# A citation the contract accepts: a file path plus a line to open (`path:line`, `path line N`, `#L5`).
# A bare filename is deliberately NOT enough, because the scorer does not credit it either — the two
# checks must agree on what evidence means, or a report can pass the linter and fail the scorer.
CITATION_RE = re.compile(
    r"`?[A-Za-z0-9_.\-/]*[A-Za-z0-9_.\-]+\.[A-Za-z0-9]+"
    r"(?::\d+(?:\s*[-–]\s*\d+)?|\s+line\s+\d+|#L\d+)`?",
    re.IGNORECASE,
)
APPROVAL_FIELD_RE = re.compile(
    r"^\s*[-*]\s*\*\*Approval\*\*\s*:?\s*`?(none|schedule|accept-risk)`?\s*$", re.IGNORECASE | re.MULTILINE
)
REVERSIBLE_FIELD_RE = re.compile(
    r"^\s*[-*]\s*\*\*Reversible\*\*\s*:?\s*`?(yes|no)`?\s*$", re.IGNORECASE | re.MULTILINE
)
VERDICT_LINE_RE = re.compile(r"\*\*(BLOCKED|CONDITIONAL|APPROVED)\*\*")
# A recovery plan is what makes `accept-risk` a decision rather than an acknowledgement.
RECOVERY_RE = re.compile(
    r"(recovery|restore|backup|snapshot|restore point|point-in-time|PITR|rollback plan|export)", re.IGNORECASE
)

PLACEHOLDER_RE = re.compile(r"<[A-Za-z][^<>\n]{2,}>|\bTODO\b|\bTBD\b")


class Problem:
    def __init__(self, where: str, message: str, fix: str) -> None:
        self.where = where
        self.message = message
        self.fix = fix

    def __str__(self) -> str:
        return f"{self.where}: {self.message}\n    fix: {self.fix}"


def split_sections(lines: list[str]) -> tuple[list[str], dict[str, tuple[int, list[str]]]]:
    """Return (preamble, {heading: (line_index_1_based, body_lines)}).

    Only `##` headings delimit sections. `###` stays inside the current section because findings are
    `###` level by contract.
    """
    preamble: list[str] = []
    sections: dict[str, tuple[int, list[str]]] = {}
    current: str | None = None
    start = 0
    body: list[str] = []
    for idx, line in enumerate(lines, start=1):
        hm = re.match(r"^##\s+(.+?)\s*$", line)
        if hm:
            if current is not None:
                sections[current] = (start, body)
            current = hm.group(1)
            start = idx
            body = []
            continue
        if current is None:
            preamble.append(line)
        else:
            body.append(line)
    if current is not None:
        sections[current] = (start, body)
    return preamble, sections


def lint(raw: str) -> list[Problem]:
    problems: list[Problem] = []
    lines = raw.splitlines()
    preamble, sections = split_sections(lines)

    # --- 1. The H1 title, and nothing before it ------------------------------
    h1 = [ln for ln in lines if re.match(r"^#\s+\S", ln)]
    if len(h1) != 1:
        problems.append(
            Problem("line 1", f"expected exactly one `# Migration Guard Report` title, found {len(h1)} H1 heading(s)",
                    "start the report with `# Migration Guard Report` and use ## only for the six sections")
        )
    elif h1[0].strip() != "# Migration Guard Report":
        problems.append(
            Problem("line 1", f"title is {h1[0].strip()!r}",
                    "use exactly `# Migration Guard Report`; a filename or date in the title is not part of the contract")
        )

    # --- 2. Exactly the six headings, in order -------------------------------
    present = list(sections.keys())
    missing = [h for h in REQUIRED_HEADINGS if h not in sections]
    extra = [h for h in present if h not in REQUIRED_HEADINGS]
    for h in missing:
        problems.append(
            Problem("headings", f"missing required section `## {h}`",
                    f"add `## {h}`; the contract has exactly six sections and a tool parses all of them")
        )
    for h in extra:
        problems.append(
            Problem("headings", f"unexpected section `## {h}`",
                    "fold its content into the most relevant finding or one of the six sections; "
                    "a Summary/Overview/Conclusion/Notes section is a contract violation")
        )
    ordered = [h for h in present if h in REQUIRED_HEADINGS]
    if ordered != [h for h in REQUIRED_HEADINGS if h in ordered]:
        problems.append(
            Problem("headings", f"sections out of order: {' > '.join(ordered)}",
                    "use the order " + " > ".join(REQUIRED_HEADINGS))
        )

    # --- 3. Verdict ----------------------------------------------------------
    if "Verdict" in sections:
        start, body = sections["Verdict"]
        text = "\n".join(body).strip()
        m = VERDICT_LINE_RE.search(text)
        first_line = next((ln for ln in body if ln.strip()), "")
        if m is None:
            problems.append(
                Problem(f"line {start + 1}", "no `**VERDICT**` token in bold at the start of the section",
                        "write `**BLOCKED** — <one sentence naming the decisive finding>`")
            )
        elif VERDICT_LINE_RE.search(first_line) is None:
            problems.append(
                Problem(f"line {start + 1}", "the verdict token is not on the first non-empty line",
                        "put `**BLOCKED**` / `**CONDITIONAL**` / `**APPROVED**` first, then the justification")
            )
        else:
            token = m.group(1)
            sev_seen = _finding_severities(sections)
            if token == "BLOCKED" and "BLOCKER" not in sev_seen:
                problems.append(
                    Problem(f"line {start + 1}", "verdict BLOCKED but no finding is BLOCKER",
                            "either add the BLOCKER finding with its evidence, or change the verdict")
                )
            if token == "APPROVED" and any(s in ("BLOCKER", "HIGH", "MEDIUM") for s in sev_seen):
                problems.append(
                    Problem(f"line {start + 1}", "verdict APPROVED but a BLOCKER/HIGH/MEDIUM finding is present",
                            "APPROVED requires no finding above LOW")
                )
            if token == "APPROVED" and "APPROVED means no blocker was found" not in text:
                problems.append(
                    Problem(f"line {start + 1}", "APPROVED without the required scope sentence",
                            "add: `APPROVED means no blocker was found in the inspected scope with the "
                            "evidence available. This is not a production guarantee.`")
                )

    # --- 4. Findings ---------------------------------------------------------
    if "Findings" in sections:
        start, body = sections["Findings"]
        problems.extend(_lint_findings(start, body))

    # --- 5. Unfilled template placeholders -----------------------------------
    for idx, line in enumerate(lines, start=1):
        if PLACEHOLDER_RE.search(line) and not re.search(r"`[^`]*<[^`]*`", line):
            problems.append(
                Problem(f"line {idx}", f"unfilled placeholder {line.strip()[:60]!r}",
                        "replace every `<...>` with the real value; a template filled with instructions is not a report")
            )

    # Structure problems are the actionable ones; report them before the title mismatch, which is
    # usually a consequence of the report being a different document entirely.
    problems.sort(key=lambda prob: 1 if prob.where == "line 1" else 0)
    return problems


def _finding_severities(sections: dict[str, tuple[int, list[str]]]) -> list[str]:
    if "Findings" not in sections:
        return []
    _, body = sections["Findings"]
    out = []
    for line in body:
        m = FINDING_HEADING_RE.match(line.strip())
        if m:
            out.append(m.group("sev"))
    return out


def _lint_findings(section_start: int, body: list[str]) -> list[Problem]:
    problems: list[Problem] = []
    blocks: list[tuple[int, str | None, str | None, list[str]]] = []
    current: tuple[int, str | None, str | None, list[str]] | None = None

    for offset, line in enumerate(body):
        lineno = section_start + 1 + offset
        stripped = line.strip()
        m = FINDING_HEADING_RE.match(stripped)
        if m:
            if current is not None:
                blocks.append(current)
            current = (lineno, m.group("sev"), m.group("cat"), [])
            continue
        lm = LOOSE_FINDING_RE.match(stripped)
        if lm:
            if current is not None:
                blocks.append(current)
            current = (lineno, None, None, [])
            problems.append(
                Problem(f"line {lineno}", f"finding heading {stripped[:70]!r} does not match the contract",
                        "use `### F<n> — SEVERITY — category`, with an em dash, the literal severity token, "
                        "and one category from the list")
            )
            continue
        if current is not None:
            current[3].append(line)
    if current is not None:
        blocks.append(current)

    if not blocks:
        problems.append(
            Problem(f"line {section_start}", "no findings",
                    "a report always has at least one finding; a clean migration gets exactly one "
                    "`### F1 — INFO — no-risk-observed` finding listing what you checked")
        )
        return problems

    for lineno, sev, cat, lines in blocks:
        if sev is None:
            continue  # already reported as a malformed heading
        if cat not in CATEGORIES:
            problems.append(
                Problem(f"line {lineno}", f"unknown category {cat!r} in the finding heading",
                        "use one of: " + ", ".join(CATEGORIES))
            )
        text = "\n".join(lines)
        for field in REQUIRED_FINDING_FIELDS:
            if not re.search(rf"^\s*[-*]\s*\*\*{field}\*\*\s*:", text, re.IGNORECASE | re.MULTILINE):
                problems.append(
                    Problem(f"line {lineno}", f"finding is missing the `**{field}:**` field",
                            _FIELD_FIX.get(field, "add the field"))
                )
        if sev in ("BLOCKER", "HIGH", "MEDIUM", "LOW"):
            ev = _field_value(text, "Evidence")
            citations = CITATION_RE.findall(ev) if ev else []
            # The scorer requires a file *and a line* inside the finding, so the linter does too. A
            # fixture filename alone (`application-usage.md`) names the file without showing that any
            # part of it was read, and `migration.sql:2` is the normal citation for the statement.
            if not citations:
                problems.append(
                    Problem(f"line {lineno}", "Evidence has no `path:line` citation",
                            "cite the file and line you read, e.g. `app/controllers/users_controller.rb:31` "
                            "or `migration.sql:2` for the statement. A bare filename is not enough, and a "
                            "finding with no citation is an Unknown, not a finding")
                )
        am = APPROVAL_FIELD_RE.search(text)
        if "**Approval**" in text and not am:
            problems.append(
                Problem(f"line {lineno}", "Approval field is not one of the three classes",
                        "write `**Approval:** none` / `schedule` / `accept-risk` exactly")
            )
        rm = REVERSIBLE_FIELD_RE.search(text)
        if "**Reversible**" in text and not rm:
            problems.append(
                Problem(f"line {lineno}", "Reversible field is not `yes` or `no`",
                        "write `**Reversible:** yes` or `**Reversible:** no`")
            )
        # Approval is decided by reversibility, not by severity. A reversible HIGH (for example an
        # unverified data property, or a failed-but-rolled-back statement) needs no human gate; a HIGH
        # whose old value cannot be restored needs `accept-risk`. Keying the check on severity refused
        # a correct report and was the cause of a false deviation on PG-002.
        if rm and rm.group(1).lower() == "no" and am and am.group(1) == "none":
            problems.append(
                Problem(f"line {lineno}", "irreversible finding declares `Approval: none`",
                        "an irreversible step always needs `accept-risk` plus a recovery plan")
            )
        if am and am.group(1) == "accept-risk" and not RECOVERY_RE.search(text):
            problems.append(
                Problem(f"line {lineno}", "`accept-risk` without a recovery plan",
                        "name the restore point and the recovery step, e.g. "
                        "`a named pg_dump of <table> taken before the drop`")
            )
    return problems


_FIELD_FIX = {
    "Statement": "write `- **Statement:** `ALTER TABLE ...` — what this statement does`",
    "Evidence": "write `- **Evidence:**` then one bullet per citation, each containing `path:line`",
    "Impact": "write `- **Impact:**` the concrete consequence if this ships as written",
    "Mitigation": "write `- **Mitigation:**` the safe alternative or the precondition",
    "Verification": "write `- **Verification:**` the command or query a human runs to confirm the mitigation",
    "Approval": "write `- **Approval:** none` / `schedule` / `accept-risk`",
    "Reversible": "write `- **Reversible:** yes` / `no`",
}


def _field_value(text: str, name: str) -> str:
    """Return the text from `**Name:**` up to the next bold field."""
    m = re.search(rf"^\s*[-*]\s*\*\*{name}\*\*\s*:?(.*)$", text, re.IGNORECASE | re.MULTILINE)
    if not m:
        return ""
    start = m.end()
    rest = text[start:]
    stop = re.search(r"^\s*[-*]\s*\*\*[A-Za-z]+\*\*", rest, re.MULTILINE)
    return m.group(1) + (rest[: stop.start()] if stop else rest)


def _safe_console() -> None:
    """A model's report may contain emoji; never crash while reporting a deviation.

    On a Windows console with a legacy code page, printing `⛔` raises `UnicodeEncodeError` in the
    middle of the failure list, which turns a clear rejection into a traceback (and can be mistaken for
    a crash rather than a finding). Print with replacement instead.
    """
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]
        except (AttributeError, ValueError):
            pass


def main(argv: list[str] | None = None) -> int:
    _safe_console()
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("path", nargs="?", help="report file, or - for stdin")
    ap.add_argument("--reports", type=Path, help="lint every report.md below this directory")
    args = ap.parse_args(argv)

    targets: list[tuple[str, str]] = []
    if args.reports:
        for p in sorted(args.reports.rglob("report.md")):
            targets.append((str(p), p.read_text(encoding="utf-8", errors="replace")))
    elif args.path == "-" or (args.path is None and not sys.stdin.isatty()):
        targets.append(("<stdin>", sys.stdin.read()))
    elif args.path:
        p = Path(args.path)
        if not p.is_file():
            print(f"not a file: {p}", file=sys.stderr)
            return 2
        targets.append((str(p), p.read_text(encoding="utf-8", errors="replace")))
    else:
        ap.print_help()
        return 2

    failed = 0
    for name, raw in targets:
        problems = lint(raw)
        if problems:
            failed += 1
            print(f"FAIL {name} — {len(problems)} deviation(s)")
            for prob in problems:
                print(f"  {prob}")
        else:
            print(f"ok   {name}")
    if len(targets) > 1:
        print(f"\n{len(targets) - failed}/{len(targets)} report(s) conform.")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
