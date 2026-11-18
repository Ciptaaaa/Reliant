#!/usr/bin/env python3
"""Run Migration Guard evaluation cases through an agent CLI in isolated scratch directories.

Why this exists
---------------
An evaluation is only meaningful if the agent under test cannot see `expected/`, cannot see the other
cases, and has no memory of a previous conversation. This script enforces all three mechanically
instead of trusting the operator:

  * each case is copied alone into a fresh temp directory
  * only `context.md`, `schema.sql`, `migration.sql`, `application-usage.md` are copied
  * the agent runs in its own session, non-interactively, with read-only tool permissions
  * the reply is saved verbatim to `evals/results/<iteration>/<CASE>/report.md`

Scoring happens separately with `scripts/evaluate_reports.py`.

Usage:
    python scripts/run_evals.py --iteration iteration-1
    python scripts/run_evals.py --iteration iteration-1 --cases PG-001 NEG-001 --runs 1
    python scripts/run_evals.py --iteration iteration-1 --dry-run

The `--dry-run` mode prints exactly what would be executed and copies the fixtures, so you can inspect
the isolation before spending tokens.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

CASE_FILES = ("context.md", "schema.sql", "migration.sql", "application-usage.md")

PROMPT_TEMPLATE = """You are reviewing a database migration before it is merged.

Case: {case_id}
Working directory files for this case:
- context.md
- schema.sql
- migration.sql
- application-usage.md

Use the migration-guard skill to analyze `migration.sql`. I want to know whether this is safe to
deploy. Follow the skill's report format exactly, quote the evidence you rely on, and tell me the
verdict."""

BASELINE_TEMPLATE = """You are reviewing a database migration before it is merged.

Case: {case_id}
Working directory files for this case:
- context.md
- schema.sql
- migration.sql
- application-usage.md

Analyze `migration.sql`. I want to know whether this is safe to deploy. Give me a report with the risks
you find, the evidence, and a verdict."""


def discover_cases(root: Path) -> list[str]:
    return sorted(p.parent.name for p in root.glob("tests/migration-guard/cases/*/*/migration.sql"))


def find_case_dir(root: Path, case_id: str) -> Path:
    hits = list(root.glob(f"tests/migration-guard/cases/*/{case_id}"))
    if not hits:
        raise SystemExit(f"case {case_id} not found")
    return hits[0]


def build_scratch(root: Path, case_id: str, skill_dir: Path | None) -> Path:
    """Copy one case (and only that case) into an isolated temp directory."""
    scratch = Path(tempfile.mkdtemp(prefix=f"reliant-{case_id}-"))
    case_dir = find_case_dir(root, case_id)
    for name in CASE_FILES:
        shutil.copy2(case_dir / name, scratch / name)
    if skill_dir is not None:
        (scratch / ".claude" / "skills").mkdir(parents=True, exist_ok=True)
        shutil.copytree(skill_dir, scratch / ".claude" / "skills" / skill_dir.name)
    # Explicitly assert the isolation this script promises.
    leaked = [p.name for p in scratch.rglob("*") if "expected" in p.name.lower()]
    if leaked:
        raise SystemExit(f"isolation failure for {case_id}: expected artefacts visible: {leaked}")
    return scratch


def build_command(prompt: str, model: str | None, allowed_tools: str) -> list[str]:
    cmd = ["claude", "-p", prompt, "--output-format", "text", "--allowedTools", *allowed_tools.split()]
    if model:
        cmd += ["--model", model]
    return cmd


def run_one(
    case_id: str,
    run_index: int,
    root: Path,
    out_root: Path,
    model: str | None,
    allowed_tools: str,
    with_skill: bool,
    dry_run: bool,
    timeout_s: int,
) -> bool:
    skill_dir = root / "skills" / "migration-guard" if with_skill else None
    scratch = build_scratch(root, case_id, skill_dir)
    prompt = (PROMPT_TEMPLATE if with_skill else BASELINE_TEMPLATE).format(case_id=case_id)
    cmd = build_command(prompt, model, allowed_tools)

    label = f"run-{run_index}" if run_index > 1 else "run-1"
    dest = out_root / case_id / label
    dest.mkdir(parents=True, exist_ok=True)

    if dry_run:
        print(f"[{case_id}/{label}] scratch={scratch}")
        print(f"[{case_id}/{label}] would run: {' '.join(cmd[:2])} … ({len(prompt)}-char prompt)")
        (dest / "dry-run.txt").write_text(
            f"scratch: {scratch}\ncommand: {json.dumps(cmd[:1])}\nfiles: {list(CASE_FILES)}\n",
            encoding="utf-8",
        )
        return True

    started = time.time()
    try:
        proc = subprocess.run(
            cmd,
            cwd=str(scratch),
            capture_output=True,
            text=True,
            timeout=timeout_s,
            encoding="utf-8",
            errors="replace",
        )
        report = proc.stdout if proc.stdout.strip() else proc.stderr
        status = proc.returncode
    except subprocess.TimeoutExpired:
        report = f"TIMEOUT after {timeout_s}s. No report produced.\n"
        status = -1

    duration = round(time.time() - started, 1)
    (dest / "report.md").write_text(report, encoding="utf-8")
    tokens = None
    for line in (proc.stdout or "").splitlines()[::-1] if status != -1 else []:
        pass  # text output mode does not expose token counts; stream-json would be needed
    (dest / "run-meta.json").write_text(
        json.dumps(
            {
                "case_id": case_id,
                "run": run_index,
                "model": model or "(cli default)",
                "agent": "claude-code",
                "date": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
                "with_skill": with_skill,
                "exit_code": status,
                "duration_s": duration,
                "tokens": tokens,
                "isolation": "single-case scratch dir, read-only tools, fresh session",
                "self_evaluated": True,
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    ok = status == 0 and len(report.strip()) > 0
    print(f"[{case_id}/{label}] exit={status} duration={duration}s report={len(report)} chars -> {dest}")
    shutil.rmtree(scratch, ignore_errors=True)
    return ok


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--iteration", default="iteration-1")
    ap.add_argument("--cases", nargs="*", help="subset of case IDs (default: all)")
    ap.add_argument("--runs", type=int, default=3, help="runs per case (default 3; outputs must be nondeterministic enough to warrant it)")
    ap.add_argument("--model", help="model passed to the agent CLI; omit for the CLI default")
    ap.add_argument("--allowed-tools", default="Read Glob Grep", help="space-separated read-only tools")
    ap.add_argument("--baseline", action="store_true", help="run without the skill, into a baseline/ folder")
    ap.add_argument("--timeout", type=int, default=600, help="per-run timeout in seconds")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv)

    root = Path.cwd()
    if shutil.which("claude") is None and not args.dry_run:
        print("claude CLI not found on PATH. Install it or use --dry-run.", file=sys.stderr)
        return 2

    cases = args.cases or discover_cases(root)
    out_root = root / "evals" / "results" / args.iteration
    if args.baseline:
        out_root = out_root / "baseline"

    print(f"{len(cases)} case(s) × {args.runs} run(s) -> {out_root}")
    print(f"tools allowed: {args.allowed_tools} | skill: {'off' if args.baseline else 'on'}")
    if args.dry_run:
        print("DRY RUN — nothing is sent to a model.\n")

    failures = 0
    for case_id in cases:
        for run_index in range(1, args.runs + 1):
            ok = run_one(
                case_id,
                run_index,
                root,
                out_root,
                args.model,
                args.allowed_tools,
                with_skill=not args.baseline,
                dry_run=args.dry_run,
                timeout_s=args.timeout,
            )
            failures += 0 if ok else 1

    if not args.dry_run and not args.baseline:
        print(f"\nScoring: python scripts/evaluate_reports.py --reports {out_root}")

    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())