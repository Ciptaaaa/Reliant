#!/usr/bin/env python3
"""Validate the Reliant repository: Agent Skills spec conformance + test fixture completeness.

This is the primary CI validator. It does not need network access and has no third-party
dependencies, so it runs anywhere Python 3.9+ is available.

Checks:
  1. Every skill under skills/ has a valid SKILL.md frontmatter (name/description rules from the
     Agent Skills specification, name == folder name).
  2. Required repository files exist.
  3. Every reference file contains a source citation section.
  4. Every test case has the four source files and both expected files.
  5. Every expected.json conforms to the structural rules in expected-schema.json and is internally
     consistent with the severity/verdict rules.
  6. Every must_cite_evidence entry resolves to a real file in the case directory.
  7. Compat layers (.claude/skills, .agents/skills) point at skills/ and do not diverge.
  8. SKILL.md stays under the recommended size (500 lines / 5000 tokens estimate).

Usage:
    python scripts/validate_repo.py [--root .] [--json OUT]
Exit code 0 when clean, 1 when problems were found.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

NAME_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
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
CASE_FILES = ("context.md", "schema.sql", "migration.sql", "application-usage.md")
REQUIRED_REPO_FILES = (
    "README.md",
    "CONTRIBUTING.md",
    "CHANGELOG.md",
    "LICENSE",
    ".gitignore",
    "docs/design-principles.md",
    "docs/evaluation.md",
    "docs/compatibility.md",
    "docs/security-model.md",
    "tests/migration-guard/README.md",
    "tests/migration-guard/expected-schema.json",
    "tests/migration-guard/harness/PROMPT.md",
    ".github/workflows/validate-skills.yml",
)
REQUIRED_REFERENCES = (
    "risk-classification.md",
    "postgres.md",
    "mysql.md",
    "safe-migration-patterns.md",
    "rollback-strategy.md",
)

problems: list[str] = []
warnings: list[str] = []


def fail(msg: str) -> None:
    problems.append(msg)


def warn(msg: str) -> None:
    warnings.append(msg)


def parse_frontmatter(text: str) -> tuple[dict, str] | tuple[None, str]:
    if not text.startswith("---"):
        return None, text
    end = text.find("\n---", 3)
    if end == -1:
        return None, text
    block = text[3:end].strip("\n")
    body = text[end + 4 :]
    data: dict = {}
    key = None
    for raw in block.splitlines():
        if not raw.strip():
            continue
        if raw.startswith("  ") and key:
            k, _, v = raw.strip().partition(":")
            data[key][k.strip()] = v.strip().strip('"')
            continue
        if ":" not in raw:
            fail(f"frontmatter: unparseable line {raw!r}")
            continue
        k, _, v = raw.partition(":")
        key = k.strip()
        v = v.strip().strip('"')
        if v == "":
            data[key] = {}
        else:
            data[key] = v
    return data, body


def check_skill(skill_dir: Path) -> None:
    skill_md = skill_dir / "SKILL.md"
    rel = skill_md.relative_to(ROOT).as_posix()
    if not skill_md.is_file():
        fail(f"{skill_dir.name}: missing SKILL.md")
        return
    text = skill_md.read_text(encoding="utf-8")
    fm, body = parse_frontmatter(text)
    if fm is None:
        fail(f"{rel}: missing or malformed YAML frontmatter")
        return

    name = fm.get("name")
    if not isinstance(name, str) or not name:
        fail(f"{rel}: missing name")
    else:
        if len(name) > 64:
            fail(f"{rel}: name longer than 64 chars")
        if not NAME_RE.match(name):
            fail(f"{rel}: name must be lowercase alphanumeric with single hyphens, got {name!r}")
        if name != skill_dir.name:
            fail(f"{rel}: name {name!r} does not match folder {skill_dir.name!r}")

    desc = fm.get("description")
    if not isinstance(desc, str) or not desc.strip():
        fail(f"{rel}: missing description")
    elif len(desc) > 1024:
        fail(f"{rel}: description longer than 1024 chars ({len(desc)})")

    for key in ("license", "compatibility", "allowed-tools"):
        if key in fm and isinstance(fm[key], str) and not fm[key]:
            warn(f"{rel}: empty optional field {key}")

    compat = fm.get("compatibility")
    if isinstance(compat, str) and len(compat) > 500:
        fail(f"{rel}: compatibility longer than 500 chars")

    meta = fm.get("metadata")
    if meta is not None:
        if not isinstance(meta, dict):
            fail(f"{rel}: metadata must be a mapping")
        else:
            for k, v in meta.items():
                if not isinstance(v, str):
                    fail(f"{rel}: metadata.{k} must be a string")

    lines = len(text.splitlines())
    words = len(re.findall(r"\S+", text))
    approx_tokens = int(words * 1.35)
    if lines > 500:
        warn(f"{rel}: {lines} lines (spec recommends under 500)")
    if approx_tokens > 5000:
        warn(f"{rel}: ~{approx_tokens} tokens (spec recommends under 5000)")

    for required_ref in REQUIRED_REFERENCES:
        if required_ref not in body:
            fail(f"{rel}: does not tell the agent when to load references/{required_ref}")

    refs_dir = skill_dir / "references"
    for required_ref in REQUIRED_REFERENCES:
        p = refs_dir / required_ref
        if not p.is_file():
            fail(f"{p.relative_to(ROOT).as_posix()}: missing required reference")
            continue
        rtext = p.read_text(encoding="utf-8")
        if not re.search(r"(https?://|docs|documentation)", rtext, re.IGNORECASE):
            fail(f"{p.relative_to(ROOT).as_posix()}: no source citation found")

    examples = skill_dir / "examples"
    if not examples.is_dir() or not any(examples.rglob("*.md")):
        warn(f"{rel}: no examples found under examples/")

    # Markdown links inside SKILL.md must resolve.
    for m in re.finditer(r"`(references/[A-Za-z0-9._-]+)`", body):
        target = skill_dir / m.group(1)
        if not target.is_file():
            fail(f"{rel}: references {m.group(1)} which does not exist")


def check_repo_files() -> None:
    for rel in REQUIRED_REPO_FILES:
        if not (ROOT / rel).is_file():
            fail(f"missing required file: {rel}")


def check_compat_layers() -> None:
    for layer in (".claude/skills", ".agents/skills"):
        base = ROOT / layer
        for skill_md in (ROOT / "skills").glob("*/SKILL.md"):
            name = skill_md.parent.name
            entry = base / name
            if not entry.exists():
                fail(f"{layer}/{name}: missing compat entry")
                continue
            target = entry / "SKILL.md"
            if not target.exists():
                fail(f"{layer}/{name}/SKILL.md: does not resolve")
                continue
            try:
                if target.resolve() != skill_md.resolve():
                    fail(f"{layer}/{name}/SKILL.md: points elsewhere")
            except OSError:
                pass
            if target.is_symlink():
                continue
            pointer = entry / "symlink.txt"
            if not pointer.is_file():
                fail(f"{layer}/{name}: not a symlink and no symlink.txt pointer for Windows checkouts")
            has_files = any(p.is_file() for p in entry.rglob("*"))
            if not has_files:
                fail(f"{layer}/{name}: compat entry contains no files")


def check_cases() -> None:
    tests = ROOT / "tests" / "migration-guard"
    case_files = list(tests.glob("cases/*/*/migration.sql"))
    ids = sorted(p.parent.name for p in case_files)
    if len(ids) != 20:
        fail(f"expected 20 test cases, found {len(ids)}")
    pg = [i for i in ids if i.startswith("PG-")]
    my = [i for i in ids if i.startswith("MY-")]
    neg = [i for i in ids if i.startswith("NEG-")]
    if len(pg) != 8:
        fail(f"expected 8 PostgreSQL cases, found {len(pg)}")
    if len(my) != 8:
        fail(f"expected 8 MySQL cases, found {len(my)}")
    if len(neg) < 4:
        fail(f"expected at least 4 negative cases, found {len(neg)}")

    seen: dict[str, str] = {}
    for case_dir in sorted({p.parent for p in case_files}):
        cid = case_dir.name
        if cid in seen:
            fail(f"duplicate case id {cid} in {case_dir} and {seen[cid]}")
        seen[cid] = str(case_dir)
        rel = case_dir.relative_to(ROOT).as_posix()
        for f in CASE_FILES:
            if not (case_dir / f).is_file():
                fail(f"{rel}: missing {f}")
        if not re.match(r"^(PG|MY|NEG)-\d{3}$", cid):
            fail(f"{rel}: case id does not match PG-NNN / MY-NNN / NEG-NNN")

        parent = case_dir.parent.name
        exp_dir = tests / "expected" / parent / cid
        for f in ("expected.md", "expected.json"):
            if not (exp_dir / f).is_file():
                fail(f"{exp_dir.relative_to(ROOT).as_posix()}: missing {f}")
        ej = exp_dir / "expected.json"
        if ej.is_file():
            check_expected(cid, case_dir, ej)

        # Every case must state engine + version or an explicit unknown.
        ctx = (case_dir / "context.md")
        if ctx.is_file():
            ctext = ctx.read_text(encoding="utf-8")
            if not re.search(r"\b(engine|postgres|mysql)\b", ctext, re.IGNORECASE):
                fail(f"{rel}/context.md: does not state the engine")


def check_expected(cid: str, case_dir: Path, ej: Path) -> None:
    rel = ej.relative_to(ROOT).as_posix()
    try:
        e = json.loads(ej.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        fail(f"{rel}: invalid JSON ({exc})")
        return

    if e.get("case_id") != cid:
        fail(f"{rel}: case_id {e.get('case_id')!r} != folder {cid!r}")
    verdict = e.get("expected_verdict")
    if verdict not in VERDICTS:
        fail(f"{rel}: invalid expected_verdict {verdict!r}")
    allowed = e.get("allowed_verdicts", [])
    if verdict not in allowed:
        fail(f"{rel}: expected_verdict not in allowed_verdicts")
    if e.get("kind") not in ("positive", "negative"):
        fail(f"{rel}: kind must be positive or negative")
    if e.get("kind") == "negative" and verdict != "APPROVED":
        fail(f"{rel}: negative cases must expect APPROVED")
    if not isinstance(e.get("destructive"), bool):
        fail(f"{rel}: destructive must be boolean")

    findings = e.get("required_findings", [])
    if not findings:
        fail(f"{rel}: required_findings is empty")
    has_blocker = False
    has_high_med = False
    approval = False
    for rf in findings:
        fid = rf.get("id", "?")
        if rf.get("severity") not in SEVERITIES:
            fail(f"{rel}: {fid} invalid severity {rf.get('severity')!r}")
        if rf.get("category") not in CATEGORIES:
            fail(f"{rel}: {fid} unknown category {rf.get('category')!r}")
        if rf.get("critical") != (rf.get("severity") == "BLOCKER"):
            fail(f"{rel}: {fid} critical must be true only for BLOCKER")
        has_blocker |= rf.get("severity") == "BLOCKER"
        has_high_med |= rf.get("severity") in ("HIGH", "MEDIUM")
        approval |= bool(rf.get("requires_human_approval"))
        for p in rf.get("must_cite_evidence", []):
            path = p.split(":")[0]
            if path and not (case_dir / path).is_file():
                fail(f"{rel}: {fid} cites non-existent evidence file {p}")
        if not rf.get("must_mention_any"):
            fail(f"{rel}: {fid} must_mention_any is empty")

    expected_verdict_from_findings = "BLOCKED" if has_blocker else ("CONDITIONAL" if has_high_med else "APPROVED")
    if verdict != expected_verdict_from_findings:
        fail(f"{rel}: verdict {verdict} contradicts findings (expected {expected_verdict_from_findings})")
    if e.get("destructive") and not approval:
        fail(f"{rel}: destructive case needs a finding with requires_human_approval")

    for fb in e.get("forbidden_findings", []):
        if not fb.get("category"):
            fail(f"{rel}: forbidden finding without category")


def main(argv: list[str] | None = None) -> int:
    global ROOT
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--root", type=Path, default=Path("."))
    ap.add_argument("--json", type=Path)
    args = ap.parse_args(argv)
    ROOT = args.root.resolve()

    check_repo_files()
    skills = [d for d in (ROOT / "skills").iterdir() if d.is_dir()]
    if not skills:
        fail("no skills found under skills/")
    for s in sorted(skills):
        check_skill(s)
        if s.name != "migration-guard":
            fail(f"phase 1 must contain exactly one skill; found {s.name}")
    check_compat_layers()
    check_cases()

    if args.json:
        args.json.write_text(json.dumps({"problems": problems, "warnings": warnings}, indent=2), encoding="utf-8")

    for w in warnings:
        print(f"WARN  {w}")
    for p in problems:
        print(f"FAIL  {p}")
    print()
    if problems:
        print(f"{len(problems)} problem(s), {len(warnings)} warning(s).")
        return 1
    print(f"Repository valid. {len(warnings)} warning(s).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
