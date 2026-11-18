#!/usr/bin/env python3
"""Fail when a Markdown file makes an unsubstantiated performance claim.

A claim like "40% fewer bugs" or "3x faster" must be on a line that also points at
`docs/evaluation.md`, where the method and the recorded runs live. Numbers that describe the current
state of the project ("20 cases", "8 PostgreSQL cases") are not claims and are ignored.

Usage:
    python scripts/check_claims.py README.md [more.md ...]
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

# Percentages and multipliers only. Counts and sizes are descriptive, not claims.
CLAIM_RE = re.compile(
    r"(\d+(?:[.,]\d+)?\s?%|\b\d+(?:[.,]\d+)?\s?[xX]\b(?![A-Za-z])|\b\d+×\b)",
)
ALLOWED_MARKERS = ("docs/evaluation.md", "evaluation.md")
# Lines that explicitly disclaim a claim are fine.
DISCLAIMERS = ("no ", "not ", "target", "targets", "never", "without")
# Percentage signs inside code blocks are usually percentages of something else (e.g. "100%" literal).
CODE_FENCE = re.compile(r"^\s*(```|~~~)")


def check(path: Path) -> list[str]:
    problems: list[str] = []
    in_code = False
    for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if CODE_FENCE.match(line):
            in_code = not in_code
            continue
        if in_code:
            continue
        for match in CLAIM_RE.finditer(line):
            window_start = max(0, match.start() - 60)
            window = line[window_start : match.end() + 60].lower()
            if any(m in window for m in ALLOWED_MARKERS):
                continue
            stripped = line.strip().lower()
            if stripped.startswith(DISCLAIMERS) or any(d in stripped.split(match.group(0).lower())[0][-40:] for d in DISCLAIMERS):
                continue
            problems.append(f"{path}:{lineno}: unsubstantiated claim {match.group(0)!r} in: {line.strip()[:120]}")
    return problems


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("files", nargs="+", type=Path)
    args = ap.parse_args(argv)
    problems: list[str] = []
    for f in args.files:
        # The evaluation document is where claims are substantiated; it is exempt by definition.
        if f.name == "evaluation.md":
            continue
        problems.extend(check(f))
    for p in problems:
        # Console encoding differs between platforms; never fail on a non-ASCII source line.
        print(p.encode("ascii", "backslashreplace").decode("ascii"))
    if problems:
        print(f"\n{len(problems)} unsubstantiated claim(s). Link to docs/evaluation.md or remove them.")
        return 1
    print(f"No unsubstantiated claims in {len(args.files)} file(s).")
    return 0


if __name__ == "__main__":
    sys.exit(main())