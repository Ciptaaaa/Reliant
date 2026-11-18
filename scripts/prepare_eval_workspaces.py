#!/usr/bin/env python3
"""Prepare isolated per-case workspaces so an agent session can review one migration.

Why this exists
---------------
An evaluation is only meaningful if the agent under test cannot see `expected/`, cannot see the other
cases, and starts with no memory of a previous conversation. This script enforces that mechanically
instead of trusting the operator.

For each case it creates:

    evals/workspace/<CASE>/context.md             the four case files, and nothing else
    evals/workspace/<CASE>/schema.sql
    evals/workspace/<CASE>/migration.sql
    evals/workspace/<CASE>/application-usage.md
    evals/workspace/<CASE>/review-prompt.md       exactly what to paste into the session
    evals/workspace/<CASE>/report-template.md     the output contract the agent must follow

It then writes `evals/workspace/INDEX.md` listing every case and every run still to record.

Usage:
    python scripts/prepare_eval_workspaces.py                 # all 20 cases
    python scripts/prepare_eval_workspaces.py --cases PG-001 MY-001
    python scripts/prepare_eval_workspaces.py --check         # verify isolation, write nothing

The saved report goes to `evals/results/<iteration>/<CASE>/run-<n>/report.md`. Score with
`scripts/evaluate_reports.py`, then lint with `scripts/linter_report.py`.

`evals/workspace/` is git-ignored on purpose: it is scratch, and it would duplicate the fixtures.
"""

from __future__ import annotations

import argparse
import re
import shutil
import sys
from pathlib import Path

CASE_FILES = ("context.md", "schema.sql", "migration.sql", "application-usage.md")
FORBIDDEN = ("expected",)

PROMPT = """You are reviewing a database migration before it is merged.

Case: {case_id}
Working directory files for this case:
- context.md
- schema.sql
- migration.sql
- application-usage.md
- report-template.md   <-- the output contract. Read it before you write anything.

Use the migration-guard skill (`skills/migration-guard/SKILL.md`) to analyze `migration.sql`.
I want to know whether this is safe to deploy.

Before you answer:
1. Read `report-template.md` and copy its skeleton.
2. Fill in every `<...>` with real values from the files you read. Cite `path:line` under **Evidence**
   for every finding, including `migration.sql:2` for the statement itself.
3. Run `python scripts/linter_report.py` on the report and fix every deviation it reports.

Return only the finished report. Do not add a summary section, a preamble, or commentary after it.
"""


def discover_cases(root: Path) -> list[str]:
    return sorted(p.parent.name for p in root.glob("tests/migration-guard/cases/*/*/migration.sql"))


CASE_ID_RE = re.compile(r"^(?:PG|MY|NEG)-\d{3}$")


def validated_case_id(case_id: str) -> str:
    """Reject anything that is not a plain case id.

    `case_id` reaches `dest_root / case_id` and then `shutil.rmtree(dest)` when `--force` is used. An
    unprotected `--cases .. --force` deleted the whole `evals/` tree during review, taking the
    uncommitted iteration-3 reports with it. A case id is a fixed, boring shape, so validate the shape
    instead of trying to sanitise a path.
    """
    if not CASE_ID_RE.match(case_id):
        raise SystemExit(
            f"refusing case id {case_id!r}: expected the form PG-001, MY-002 or NEG-003"
        )
    return case_id


def find_case_dir(root: Path, case_id: str) -> Path:
    validated_case_id(case_id)
    hits = [p for p in root.glob(f"tests/migration-guard/cases/*/{case_id}") if p.is_dir()]
    if not hits:
        raise SystemExit(f"case {case_id} not found")
    return hits[0]


def build(root: Path, case_id: str, dest_root: Path, force: bool) -> Path:
    validated_case_id(case_id)
    src = find_case_dir(root, case_id)
    dest_root = dest_root.resolve()
    dest = (dest_root / case_id).resolve()
    # Belt and braces: even a valid-looking id must land directly inside the workspace root. If this
    # ever fails, the input validation above has a hole and `rmtree` must not run.
    if dest.parent != dest_root:
        raise SystemExit(f"refusing to write {dest}: not directly inside {dest_root}")
    if dest.exists():
        if not force:
            raise SystemExit(f"{dest} already exists; pass --force to recreate it")
        # Only ever delete a directory this script owns: it must look like a workspace it built.
        if not (dest / "review-prompt.md").is_file():
            raise SystemExit(
                f"refusing to delete {dest}: it does not look like a prepared workspace "
                f"(no review-prompt.md)"
            )
        shutil.rmtree(dest)
    dest.mkdir(parents=True)
    for name in CASE_FILES:
        shutil.copy2(src / name, dest / name)
    shutil.copy2(root / "skills" / "migration-guard" / "report-template.md", dest / "report-template.md")
    (dest / "review-prompt.md").write_text(PROMPT.format(case_id=case_id), encoding="utf-8")

    # Assert the isolation this script promises. A leaked `expected/` file would invalidate the run.
    leaked = [str(p.relative_to(dest)) for p in dest.rglob("*") if any(f in p.name.lower() for f in FORBIDDEN)]
    if leaked:
        raise SystemExit(f"isolation failure for {case_id}: {leaked}")
    return dest


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--root", type=Path, default=Path.cwd())
    ap.add_argument("--cases", nargs="*", help="subset of case ids (default: all)")
    ap.add_argument("--force", action="store_true", help="recreate workspaces that already exist")
    ap.add_argument("--check", action="store_true", help="only verify the repository layout, write nothing")
    args = ap.parse_args(argv)

    root = args.root.resolve()
    cases = args.cases or discover_cases(root)

    if args.check:
        ok = True
        for cid in cases:
            case_dir = find_case_dir(root, cid)
            missing = [f for f in CASE_FILES if not (case_dir / f).is_file()]
            if missing:
                print(f"FAIL {cid}: missing {missing}")
                ok = False
            leaked = [p for p in case_dir.rglob("*") if any(f in p.name.lower() for f in FORBIDDEN)]
            if leaked:
                print(f"FAIL {cid}: case directory contains expected artefacts: {leaked}")
                ok = False
        template = root / "skills" / "migration-guard" / "report-template.md"
        if not template.is_file():
            print("FAIL skills/migration-guard/report-template.md is missing")
            ok = False
        print(f"{len(cases)} case(s) checked: {'ok' if ok else 'problems found'}")
        return 0 if ok else 1

    dest_root = root / "evals" / "workspace"
    rows = []
    for cid in cases:
        dest = build(root, cid, dest_root, args.force)
        rows.append(cid)
        print(f"prepared {dest.relative_to(root).as_posix()}")

    index = ["# Evaluation workspaces", "",
             "One directory per case. Each contains the four migration files, `report-template.md`, and",
             "`review-prompt.md` to paste into a fresh session.", "",
             "Save each run to `evals/results/<iteration>/<CASE>/run-<n>/report.md`, then score:", "",
             "```bash",
             "python scripts/evaluate_reports.py --reports evals/results/<iteration> --report-only",
             "python scripts/linter_report.py --reports evals/results/<iteration>",
             "```", "", "| Case | Workspace |", "|---|---|"]
    index += [f"| {cid} | `evals/workspace/{cid}/` |" for cid in rows]
    (dest_root / "INDEX.md").write_text("\n".join(index) + "\n", encoding="utf-8")
    print(f"\n{len(rows)} workspace(s) under {dest_root.relative_to(root).as_posix()}/ (git-ignored)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
