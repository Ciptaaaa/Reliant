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
  9. The report contract is consistent: the good example conforms, the bad example is rejected, and
     every embedded example report in examples/ conforms. Without this, the documentation drifts from
     the contract again — which is exactly what produced 0% format compliance in iterations 1-2.

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

sys.path.insert(0, str(Path(__file__).resolve().parent))
import linter_report  # noqa: E402  (same directory; stdlib-only module)

NAME_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
SEVERITIES = ("BLOCKER", "HIGH", "MEDIUM", "LOW", "INFO")
APPROVAL_CLASSES = ("none", "schedule", "accept-risk")
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


def check_report_contract() -> None:
    """The documented contract, the shipped examples, and the linter must agree.

    A skill whose examples do not follow its own contract teaches the model to ignore the contract.
    That is diagnosed in docs/evaluation.md as the cause of the 0% format compliance in iterations 1-2,
    so it is a CI failure now, not a review comment.
    """
    good = ROOT / "skills" / "migration-guard" / "examples" / "good-report.report.md"
    bad = ROOT / "skills" / "migration-guard" / "examples" / "bad-report.report.md"
    if not good.is_file():
        fail("examples/good-report.report.md: missing conforming example")
    else:
        deviations = linter_report.lint(good.read_text(encoding="utf-8"))
        if deviations:
            # Report the deviation whose location is most informative; `where` already starts with
            # the line number or "headings" so the message points at the real cause.
            fail(f"{good.relative_to(ROOT).as_posix()}: conforming example does not conform — "
                 f"{deviations[0].where}: {deviations[0].message}")
    if not bad.is_file():
        fail("examples/bad-report.report.md: missing rejected example")
    elif not linter_report.lint(bad.read_text(encoding="utf-8")):
        fail(f"{bad.relative_to(ROOT).as_posix()}: rejected example unexpectedly conforms; it can no longer teach")

    for p in sorted((ROOT / "skills" / "migration-guard" / "examples").rglob("*.md")):
        if p.name.endswith((".report.md", "bad-report.md", "good-report.md")):
            continue
        blocks = re.findall(r"```markdown\n(.*?)\n```", p.read_text(encoding="utf-8"), re.S)
        for i, block in enumerate(blocks, start=1):
            deviations = linter_report.lint(block)
            if deviations:
                fail(f"{p.relative_to(ROOT).as_posix()} (embedded report #{i}): {deviations[0]}")

    # The single finding example inside SKILL.md is the most imitated block in the repository. When it
    # cited `schema.sql` without a line, every report imitated that and failed the evidence check. It is
    # a fragment (no six headings), so only the finding-level rules apply.
    skill_md = ROOT / "skills" / "migration-guard" / "SKILL.md"
    match = re.search(r"```markdown\n(### F1.*?)\n```", skill_md.read_text(encoding="utf-8"), re.S)
    if not match:
        fail("SKILL.md: the finding example block is missing")
    else:
        example = match.group(1)
        for message in _finding_example_problems(example):
            fail(f"SKILL.md finding example: {message}")


def _finding_example_problems(example: str) -> list[str]:
    """Check a finding fragment against the finding-level contract.

    A fragment has no `## Scope`, so the six-heading rules do not apply. Everything that governs a
    single finding does: the heading shape, a known category, the seven fields, and a `path:line`
    citation in `Evidence`.
    """
    problems: list[str] = []
    first = example.splitlines()[0].strip()
    heading = re.match(r"^### F\d+ \u2014 (BLOCKER|HIGH|MEDIUM|LOW|INFO) \u2014 ([a-z][a-z0-9-]+)$", first)
    if not heading:
        problems.append(f"heading {first!r} is not `### F<n> — SEVERITY — category`")
    elif heading.group(2) not in linter_report.CATEGORIES:
        problems.append(f"unknown category {heading.group(2)!r}")
    for field_name in linter_report.REQUIRED_FINDING_FIELDS:
        if not re.search(rf"^\s*[-*]\s*\*\*{field_name}\*\*\s*:", example, re.IGNORECASE | re.MULTILINE):
            problems.append(f"missing `**{field_name}:**`")
    evidence = linter_report._field_value(example, "Evidence")
    if evidence and not linter_report.CITATION_RE.findall(evidence):
        problems.append("Evidence has no `path:line` citation")
    else:
        # Every bullet under **Evidence** needs its own line number. One good citation must not cover
        # for a neighbour that names a file without a line — that is exactly how the example taught the
        # habit that produced 4% evidence coverage.
        for bullet in re.findall(r"^\s+[-*]\s+(.+)$", evidence, re.MULTILINE):
            if not linter_report.CITATION_RE.search(bullet):
                problems.append(f"Evidence bullet has no `path:line`: {bullet.strip()[:60]!r}")
    return problems


def check_kilo_config() -> None:
    """Kilo Code loads skills through `kilo.json` `skills.paths`.

    This is the only discovery path that works in a fresh checkout without duplication, so it must
    resolve. A typo here means the skill is invisible to Kilo while every other check still passes —
    which is exactly what happened before this check existed.
    """
    config = ROOT / "kilo.json"
    if not config.is_file():
        warn("kilo.json: missing; Kilo Code will not discover the skill without .kilo/skills or a link")
        return
    try:
        data = json.loads(config.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        fail(f"kilo.json: invalid JSON ({exc})")
        return
    paths = (data.get("skills") or {}).get("paths") or []
    if not paths:
        warn("kilo.json: no skills.paths; Kilo Code will not discover the skill automatically")
        return
    for entry in paths:
        resolved = (ROOT / entry).resolve()
        if not resolved.is_dir():
            fail(f"kilo.json: skills path {entry!r} does not exist")
            continue
        names = {p.parent.name for p in resolved.glob("*/SKILL.md")}
        if not names:
            fail(f"kilo.json: skills path {entry!r} contains no <name>/SKILL.md")
            continue
        # Frontmatter `name` must match the directory name (base spec and Kilo both require it).
        for p in resolved.glob("*/SKILL.md"):
            fm, _body = parse_frontmatter(p.read_text(encoding="utf-8"))
            declared = (fm or {}).get("name")
            if declared != p.parent.name:
                fail(f"{p.relative_to(ROOT).as_posix()}: name {declared!r} does not match "
                     f"directory {p.parent.name!r}; Kilo Code rejects a mismatch")


def check_repo_files() -> None:
    for rel in REQUIRED_REPO_FILES:
        if not (ROOT / rel).is_file():
            fail(f"missing required file: {rel}")


def check_compat_layers() -> None:
    """Check the discovery layer, and report honestly whether it is active.

    Two different questions, and conflating them hid a real defect:

    1. *Is the repository layout consistent?* Every compat entry must be either a link whose `SKILL.md`
       resolves to the source skill, or a pointer file naming it. A missing entry, or one that points
       elsewhere, is a failure.
    2. *Can an agent actually find the skill?* Only a real link answers yes. A pointer file is a note to
       the reader telling them to create the link themselves.

    A link here means either a POSIX symlink or a Windows directory junction. A junction is not
    `is_symlink()` on Windows, and an earlier version required `is_symlink()`, so the junction setup the
    docs tell Windows users to create failed validation while the inactive pointer file passed. What
    matters is that `SKILL.md` resolves to the source file, not which mechanism produced it.

    Question 2 is reported as a warning naming the consequence, because with `core.symlinks=false` — which
    this repository has — a fresh checkout cannot discover the skill without manual setup.
    """
    pointer_only: list[str] = []
    for layer in (".claude/skills", ".agents/skills"):
        base = ROOT / layer
        for skill_md in (ROOT / "skills").glob("*/SKILL.md"):
            name = skill_md.parent.name
            entry = base / name
            if not entry.exists() and not entry.is_symlink():
                fail(f"{layer}/{name}: missing compat entry")
                continue

            target = entry / "SKILL.md"
            if target.exists():
                try:
                    resolves_to_source = target.resolve() == skill_md.resolve()
                except OSError:
                    resolves_to_source = False
                if not resolves_to_source:
                    fail(f"{layer}/{name}/SKILL.md: points elsewhere")
                    continue
                # Symlink or junction: the contents come from the source skill, so it is active.
                continue

            # No SKILL.md here: the entry must be a pointer file for checkouts without link support.
            pointer = entry / "symlink.txt"
            if not pointer.is_file():
                fail(f"{layer}/{name}: not a link and no symlink.txt pointer for checkouts without links")
                continue
            if "skills/migration-guard" not in pointer.read_text(encoding="utf-8"):
                fail(f"{layer}/{name}/symlink.txt: does not name the source skill")
                continue
            pointer_only.append(f"{layer}/{name}")

    if pointer_only:
        noun = "entry is a pointer file" if len(pointer_only) == 1 else "entries are pointer files"
        warn(
            f"{len(pointer_only)} compat {noun}, not links ({', '.join(pointer_only)}): an agent that "
            f"discovers skills under those paths will not find this one without manual setup. "
            f"See docs/compatibility.md."
        )


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

    irreversible_cases = 0

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
            if check_expected(cid, case_dir, ej):
                irreversible_cases += 1

        # Every case must state engine + version or an explicit unknown.
        ctx = (case_dir / "context.md")
        if ctx.is_file():
            ctext = ctx.read_text(encoding="utf-8")
            if not re.search(r"\b(engine|postgres|mysql)\b", ctext, re.IGNORECASE):
                fail(f"{rel}/context.md: does not state the engine")

    # The denominator is part of the result: `destructive_policy_compliance` is computed over these
    # cases, so a reader must be able to see how many there are. Printing it on every run means a change
    # to the fixture set shows up as a changed denominator instead of a silently different metric.
    print(f"irreversible cases (destructive-policy denominator): {irreversible_cases} of {len(ids)}")


def check_expected(cid: str, case_dir: Path, ej: Path) -> bool:
    """Validate one expected.json. Returns True when the case is irreversible.

    The return value is the destructive-policy denominator, which `check_cases` reports, so the size
    of that metric is visible on every run instead of being buried in a count nobody recomputes.
    """
    rel = ej.relative_to(ROOT).as_posix()
    try:
        e = json.loads(ej.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        fail(f"{rel}: invalid JSON ({exc})")
        return False

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
    # `irreversible` is the current field; `destructive` is the deprecated alias and must agree with it.
    irreversible = e.get("irreversible", e.get("destructive"))
    if not isinstance(irreversible, bool):
        fail(f"{rel}: irreversible must be boolean")
    if "destructive" in e and e["destructive"] != irreversible:
        fail(f"{rel}: destructive and irreversible disagree")

    findings = e.get("required_findings", [])
    if not findings:
        fail(f"{rel}: required_findings is empty")
    # Duplicate ids make a finding impossible to address in a report and double-count it in recall.
    seen_ids: dict[str, int] = {}
    for rf in findings:
        fid = rf.get("id", "?")
        seen_ids[fid] = seen_ids.get(fid, 0) + 1
    for fid, count in seen_ids.items():
        if count > 1:
            fail(f"{rel}: finding id {fid!r} appears {count} times; ids must be unique within a case")
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
        approval_class = rf.get("approval")
        if approval_class is not None and approval_class not in APPROVAL_CLASSES:
            fail(f"{rel}: {fid} invalid approval {approval_class!r}")
        if approval_class == "accept-risk" or rf.get("requires_human_approval"):
            approval = True
        if approval_class == "none" and rf.get("reversible") is False:
            fail(f"{rel}: {fid} approval 'none' contradicts reversible=false")
        if rf.get("reversible") is False and approval_class in (None, "none") and not rf.get("requires_human_approval"):
            fail(f"{rel}: {fid} irreversible step needs an approval class of 'schedule' or 'accept-risk'")
        # Same rule from the other side: asking a human to accept risk for a reversible change is
        # over-caution, and the taxonomy in references/change-impact-taxonomy.md §4 calls it a defect.
        if rf.get("reversible") is True and approval_class == "accept-risk":
            fail(f"{rel}: {fid} reversible=true contradicts approval='accept-risk' "
                 f"(reversible costly work is 'schedule'; only lost data needs 'accept-risk')")
        for p in rf.get("must_cite_evidence", []):
            path = p.split(":")[0]
            if path and not (case_dir / path).is_file():
                fail(f"{rel}: {fid} cites non-existent evidence file {p}")
        if not rf.get("must_mention_any"):
            fail(f"{rel}: {fid} must_mention_any is empty")

    expected_verdict_from_findings = "BLOCKED" if has_blocker else ("CONDITIONAL" if has_high_med else "APPROVED")
    if verdict != expected_verdict_from_findings:
        fail(f"{rel}: verdict {verdict} contradicts findings (expected {expected_verdict_from_findings})")
    if irreversible and not approval:
        fail(f"{rel}: irreversible case needs a finding with approval 'accept-risk' (or requires_human_approval)")
    # A case-level `irreversible: true` must be backed by at least one finding that actually says so.
    # Otherwise `destructive_policy_compliance` counts a case whose reports can never carry a gate for
    # it, and the denominator measures nothing (found by the taxonomy audit, `docs/evaluation.md`).
    if irreversible and not any(rf.get("reversible") is False for rf in findings):
        fail(f"{rel}: irreversible=true but no finding declares reversible=false; "
             f"either flag the finding or set irreversible=false")
    for fb in e.get("forbidden_findings", []):
        if not fb.get("category"):
            fail(f"{rel}: forbidden finding without category")

    # Returned, not counted here: `check_cases` reports the total.
    return bool(irreversible)


def _safe_console() -> None:
    """Never lose a failure message because a model wrote an emoji into a file.

    Reports, expected.md files and example reports all contain non-ASCII text. On a Windows console
    with a legacy code page, printing one raises `UnicodeEncodeError` and the problem list — the reason
    the run failed — is never shown.
    """
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]
        except (AttributeError, ValueError):
            pass


def main(argv: list[str] | None = None) -> int:
    _safe_console()
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
    check_kilo_config()
    check_cases()
    check_report_contract()

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
