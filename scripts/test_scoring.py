#!/usr/bin/env python3
"""Regression tests for the scorer and the report linter.

Each test pins a defect that was found by review or by running an iteration, so the defect cannot
silently return. These are deliberately small and dependency-free: the repo has no test framework and
adding one for six assertions would be heavier than the value it provides.

Usage:
    python scripts/test_scoring.py          # exit 0 when all pass
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import evaluate_reports as E  # noqa: E402
import linter_report as L  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
CASES = ROOT / "tests" / "migration-guard" / "cases"
EXPECTED = ROOT / "tests" / "migration-guard" / "expected"

failures: list[str] = []


def check(name: str, condition: bool, detail: str = "") -> None:
    if condition:
        print(f"ok   {name}")
    else:
        failures.append(f"{name}: {detail}")
        print(f"FAIL {name}  {detail}")


REPORT_TEMPLATE = """# Migration Guard Report

## Scope
- **Migration**: `db/migrate/x.sql`

## Verdict
**BLOCKED** — the column is still read.

## Findings

### F1 — BLOCKER — destructive-column-drop
- **Statement**: `ALTER TABLE users DROP COLUMN phone` in `migration.sql:2`
- **Evidence**:
  - `application-usage.md` — `app/controllers/users_controller.rb:31` reads `phone`
- **Impact**: the profile endpoint raises
- **Mitigation**: retire readers first; restore from the named `pg_dump`
- **Verification**: `grep -rn "phone" app/`
- **Approval**: accept-risk
- **Reversible**: no

{f2}## Safer migration path
1. Expand and contract.

## Rollback and recovery
- Schema rollback: re-add the column.

## Pre-deploy checklist
- [ ] Approval recorded
"""

F2_BLOCK = """### F2 — HIGH — data-integrity
- **Statement**: the same statement destroys the stored values
- **Evidence**:
  - `schema.sql` — 240,000 non-NULL values
- **Impact**: values cannot be restored by re-adding the column
- **Mitigation**: take a named `pg_dump` before the drop
- **Verification**: the dump restores into scratch
- **Approval**: {approval}
- **Reversible**: no

"""


def pg001() -> tuple[dict, Path]:
    exp = json.loads((EXPECTED / "postgres" / "PG-001" / "expected.json").read_text(encoding="utf-8"))
    return exp, CASES / "postgres" / "PG-001"


def score(f2_approval: str | None):
    exp, case_dir = pg001()
    block = F2_BLOCK.format(approval=f2_approval) if f2_approval else ""
    text = REPORT_TEMPLATE.format(f2=block)
    return E.score_case(exp, E.parse_report(text), case_dir)


def test_destructive_denominator() -> None:
    """review finding #2: omitting an irreversible finding must not raise the ratio."""
    present_good = score("accept-risk")
    present_bad = score("none")
    deleted = score(None)
    check("destructive: correct gates pass", present_good.destructive_ok is True,
          f"got {present_good.destructive_ok}")
    check("destructive: wrong gate fails", present_bad.destructive_ok is False,
          f"got {present_bad.destructive_ok}")
    check("destructive: deleting the finding still fails", deleted.destructive_ok is False,
          "omission was rewarded (denominator came from matched findings)")
    check("destructive: denominator comes from the fixture",
          present_good.destructive_total == 2, f"got {present_good.destructive_total}")


def test_evidence_requires_a_line() -> None:
    """review finding #4: a bare filename is not a citation."""
    check("citation: bare filename rejected", E._has_citation("see `migration.sql` for it") is False,
          "a bare filename satisfied the evidence check")
    check("citation: path:line accepted", E._has_citation("see `migration.sql:2` for it") is True,
          "a real path:line was rejected")


def test_negated_approval() -> None:
    """review finding #5: negations longer than the old 24-char window."""
    negatives = [
        "No human approval is required for this step.",
        "There is no need for a human approval.",
        "Do not approve before the backfill completes.",
        "It does not have to accept the risk.",
        "No approval gate is needed here.",
    ]
    positives = [
        "This needs human approval before the backfill.",
        "A human should approve before the drop.",
        "Requires approval from the DBA.",
    ]
    for phrase in negatives:
        check(f"approval: negation not a gate {phrase!r}", E._has_approval_gate(phrase) is False,
              "a negated phrase was counted as an approval gate")
    for phrase in positives:
        check(f"approval: positive is a gate {phrase!r}", E._has_approval_gate(phrase) is True,
              "a real approval requirement was missed")


def test_format_check_can_fail() -> None:
    """review: `check_format` must reject a reordered contract, not only a missing heading."""
    reordered = (
        REPORT_TEMPLATE.format(f2="")
        .replace("## Scope", "## TEMP")
        .replace("## Verdict", "## Scope")
        .replace("## TEMP", "## Verdict")
    )
    ok, notes = E.check_format(E.parse_report(reordered))
    check("format: reordered sections are rejected", ok is False,
          "out-of-order headings were accepted")
    ok_good, _ = E.check_format(E.parse_report(REPORT_TEMPLATE.format(f2="")))
    check("format: the contract example is accepted", ok_good is True,
          "a conforming report was rejected")


def test_linter_and_scorer_agree() -> None:
    """review: a report the linter accepts must not be failed by the scorer's format check, and vice versa.

    Checked on the two shipped examples, which are the documents most likely to drift.
    """
    for name, should_conform in (
        ("good-report.report.md", True),
        ("bad-report.report.md", False),
    ):
        path = ROOT / "skills" / "migration-guard" / "examples" / name
        raw = path.read_text(encoding="utf-8")
        lint_ok = not L.lint(raw)
        fmt_ok, _ = E.check_format(E.parse_report(raw))
        check(f"agreement: linter verdict for {name}", lint_ok is should_conform,
              f"linter said {lint_ok}, expected {should_conform}")
        check(f"agreement: scorer agrees with linter for {name}", fmt_ok is lint_ok,
              f"linter={lint_ok} scorer={fmt_ok} — the two read different contracts")


def test_evidence_bullets_each_cite_a_line() -> None:
    """Every Evidence bullet needs its own line number; one good citation must not cover a neighbour.

    The SKILL.md example cited `schema.sql:4` correctly but the habit came from an earlier version that
    wrote `schema.sql` alone. A single satisfied citation in the field hid the bullet without one, so the
    example passed while teaching the wrong form.
    """
    import validate_repo as V

    good = """### F1 — BLOCKER — destructive-column-drop
- **Statement**: `ALTER TABLE users DROP COLUMN phone` in `migration.sql:2`
- **Evidence**:
  - `app/controllers/users_controller.rb:31` — reads `phone`
  - `schema.sql:4` — 240,000 non-NULL values
- **Impact**: x
- **Mitigation**: y
- **Verification**: z
- **Approval**: accept-risk
- **Reversible**: no"""
    bad = good.replace("  - `schema.sql:4` — 240,000 non-NULL values", "  - `schema.sql` — 240,000 non-NULL values")

    check("evidence: every bullet cited is accepted", V._finding_example_problems(good) == [],
          f"got {V._finding_example_problems(good)}")
    bad_problems = V._finding_example_problems(bad)
    check("evidence: a bullet without a line is rejected", any("no `path:line`" in p for p in bad_problems),
          f"one good citation covered a bad bullet: {bad_problems}")


def test_fixture_runner_mysql_uri() -> None:
    """review/CI: a mysql:// URI must become client flags, not a database name.

    The failing GitHub Actions run had every MY case die with `Can't connect to local server through
    socket '/run/mysqld/mysqld.sock'` because `mysql "mysql://..."` makes the client look for a socket
    instead of the TCP host in the URI. The parser is pure and testable without a database.
    """
    import check_fixtures as C

    args = C.mysql_args("mysql://root:secret@db.example:3307/nightly")
    joined = " ".join(args)
    check("fixtures: host flag parsed", "--host=db.example" in joined, joined)
    check("fixtures: port flag parsed", "--port=3307" in joined, joined)
    check("fixtures: user flag parsed", "--user=root" in joined, joined)
    check("fixtures: database flag parsed", "--database=nightly" in joined, joined)
    check("fixtures: URI not passed through", not any(a.startswith("mysql://") for a in args), joined)

    defaults = " ".join(C.mysql_args("mysql://root:pw@localhost/db"))
    check("fixtures: default port applied", "--port=3306" in defaults, defaults)
    # The admin connection (CREATE/DROP DATABASE) must not select the URI's database, or the statement
    # would run inside the schema it is trying to replace.
    admin = " ".join(C.mysql_args("mysql://root:pw@localhost/db", database=None))
    check("fixtures: admin connection has no --database", "--database=" not in admin, admin)


def test_fixture_namespaces_are_unique() -> None:
    """CI: every fixture must get its own namespace.

    The failing GitHub Actions run showed `relation "users" already exists` from PG-007 onward and
    `customers already exists` for PG-004: all cases were applied to one shared database. Isolation is
    what makes a fixture reproducible on its own, so it is asserted here rather than assumed.
    """
    import re

    import check_fixtures as C

    cases = sorted(p.parent.name for p in Path("tests/migration-guard/cases").glob("*/*/migration.sql"))
    namespaces = [C._schema_name(cid) for cid in cases]
    check("fixtures: one namespace per case", len(set(namespaces)) == len(cases),
          f"{len(cases)} cases produced {len(set(namespaces))} namespaces")
    invalid = [n for n in namespaces if not re.fullmatch(r"[a-z_][a-z0-9_]*", n)]
    check("fixtures: namespaces are valid identifiers", not invalid, f"invalid: {invalid}")


def main() -> int:
    test_destructive_denominator()
    test_evidence_requires_a_line()
    test_evidence_bullets_each_cite_a_line()
    test_negated_approval()
    test_format_check_can_fail()
    test_linter_and_scorer_agree()
    test_fixture_runner_mysql_uri()
    test_fixture_namespaces_are_unique()
    print()
    if failures:
        print(f"{len(failures)} check(s) failed:")
        for f in failures:
            print(f"  - {f}")
        return 1
    print("All checks passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
