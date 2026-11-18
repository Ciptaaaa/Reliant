#!/usr/bin/env python3
"""Run Migration Guard evaluation cases through an OpenAI-compatible chat API.

Why a separate runner from run_evals.py
----------------------------------------
A real Agent Skills session gives the model tools (Read/Glob/Grep) and lets it load
`references/` on demand. A single chat completion cannot do that, so this runner makes a
different, clearly-labelled trade-off:

    system prompt = SKILL.md + every reference file (in full)
    user prompt   = the four case files, verbatim
    tools         = none

Effect on results: references are provided up front instead of being pulled on demand. That
removes the "did the model load the right reference?" failure mode and therefore *inflates*
recall relative to a real session. It does not test tool use at all. Any score produced here
must be recorded as "single-turn, no tools, references preloaded".

Isolation: each case is sent alone, with no conversation history and no access to `expected/`.

Usage:
    python scripts/run_evals_api.py --iteration api-1 --pilot
    python scripts/run_evals_api.py --iteration api-1
    python scripts/run_evals_api.py --iteration api-1 --cases PG-001 --runs 1

Reads the endpoint and token from the same environment the Claude CLI uses
(ANTHROPIC_BASE_URL / ANTHROPIC_AUTH_TOKEN) or from --base-url / --api-key.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

CASE_FILES = ("context.md", "schema.sql", "migration.sql", "application-usage.md")
REFERENCE_ORDER = (
    "risk-classification.md",
    "postgres.md",
    "mysql.md",
    "safe-migration-patterns.md",
    "rollback-strategy.md",
)

USER_TEMPLATE = """Review this database migration before it is merged.

Case: {case_id}

--- application-usage.md ---
{usage}

--- schema.sql ---
{schema}

--- migration.sql ---
{migration}

--- context.md ---
{context}

Produce the Migration Guard report for `migration.sql`, following the skill's report format exactly.
Quote the evidence you rely on and state the verdict."""


def load_report_template(root: Path) -> str:
    """The contract, verbatim, for the API runner.

    In a real Agent Skills session the agent reads `report-template.md` with a file tool. This runner
    has no tools, so the file must be in the prompt. Leaving it out was the difference between "the
    contract exists" and "the model can see the contract": the pilot run still produced 0% format
    compliance because the instruction said "open report-template.md" and no such file was reachable.
    """
    path = root / "skills" / "migration-guard" / "report-template.md"
    return path.read_text(encoding="utf-8")


def contract_banner(template: str) -> str:
    """A short, unmissable restatement placed first and last in the system prompt.

    Position matters as much as content: a 70K prompt buries a rule stated once in the middle. The
    contract is therefore repeated before the references and again after SKILL.md.
    """
    headings = (
        "# Migration Guard Report\n"
        "## Scope\n"
        "## Verdict\n"
        "## Findings\n"
        "## Safer migration path\n"
        "## Rollback and recovery\n"
        "## Pre-deploy checklist"
    )
    return (
        "OUTPUT CONTRACT — read this first and follow it exactly.\n"
        "Your reply is a Markdown report with exactly these seven lines-as-headings, in this order,\n"
        "and no other `##` heading:\n\n"
        f"```text\n{headings}\n```\n\n"
        "Each finding under `## Findings` is `### F<n> — SEVERITY — category` followed by the seven\n"
        "fields `**Statement**`, `**Evidence**` (one bullet per `path:line` citation), `**Impact**`,\n"
        "`**Mitigation**`, `**Verification**`, `**Approval**` (none | schedule | accept-risk),\n"
        "`**Reversible**` (yes | no). The verdict is `**BLOCKED**` / `**CONDITIONAL**` /\n"
        "`**APPROVED**` on the first line of `## Verdict`.\n\n"
        "Do not write `## Evidence`, `## Summary`, `## Notes`, `## Conclusion`, `## Blast radius`, or a\n"
        "numbered `## 1. ...` analysis. That content belongs inside a finding.\n\n"
        f"The full template follows; `\n{'-' * 70}` marks its start.\n"
        f"{'-' * 70}\n{template}\n{'-' * 70}\n\n"
    )


def load_skill_context(root: Path) -> str:
    """Build the system prompt for the API runner.

    Order, and why:
      1. The output contract (short form + the full template), first.
      2. The references, as background.
      3. SKILL.md, the instruction document.
      4. The output contract again, last.

    Iterations 1-2 put the contract only inside SKILL.md, in the middle of a ~70K prompt, and every
    one of 60 runs restyled the report. Recency and primacy both matter for long prompts, so the
    contract is stated at both ends and the full template is included — this runner has no tools, so
    "open report-template.md" is not an instruction the model can obey.
    """
    skill_dir = root / "skills" / "migration-guard"
    banner = contract_banner(load_report_template(root))
    parts: list[str] = [banner]
    for name in REFERENCE_ORDER:
        path = skill_dir / "references" / name
        parts.append(f"--- references/{name} (background) ---\n\n{path.read_text(encoding='utf-8')}\n\n")
    parts.append("--- SKILL.md (the instructions you must follow) ---\n\n")
    parts.append(skill_dir.joinpath("SKILL.md").read_text(encoding="utf-8"))
    parts.append(
        "\n\n--- FINAL REMINDER: the output contract ---\n\n"
        "Reply with exactly: `# Migration Guard Report`, then `## Scope`, `## Verdict`, `## Findings`,\n"
        "`## Safer migration path`, `## Rollback and recovery`, `## Pre-deploy checklist`. No other\n"
        "`##` heading. Every finding is `### F<n> — SEVERITY — category` with `**Statement**`,\n"
        "`**Evidence**`, `**Impact**`, `**Mitigation**`, `**Verification**`, `**Approval**`,\n"
        "`**Reversible**`. The verdict is `**BLOCKED**`, `**CONDITIONAL**` or `**APPROVED**` on the\n"
        "first line of `## Verdict`. A summary section is not part of the contract.\n"
    )
    return "".join(parts)


def find_case_dir(root: Path, case_id: str) -> Path:
    hits = list(root.glob(f"tests/migration-guard/cases/*/{case_id}"))
    if not hits:
        raise SystemExit(f"case {case_id} not found")
    return hits[0]


def discover_cases(root: Path) -> list[str]:
    return sorted(p.parent.name for p in root.glob("tests/migration-guard/cases/*/*/migration.sql"))


def build_user_prompt(case_dir: Path, case_id: str) -> str:
    return USER_TEMPLATE.format(
        case_id=case_id,
        usage=(case_dir / "application-usage.md").read_text(encoding="utf-8"),
        schema=(case_dir / "schema.sql").read_text(encoding="utf-8"),
        migration=(case_dir / "migration.sql").read_text(encoding="utf-8"),
        context=(case_dir / "context.md").read_text(encoding="utf-8"),
    )


def call_api(base_url: str, api_key: str, model: str, system: str, user: str, timeout: int) -> tuple[int, dict]:
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        "max_tokens": 4000,
    }
    req = urllib.request.Request(
        f"{base_url.rstrip('/')}/chat/completions",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {api_key}"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", "replace")[:500]
        return exc.code, {"error": body}
    except Exception as exc:  # network, timeout, JSON
        return -1, {"error": str(exc)[:500]}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--iteration", default="api-1")
    ap.add_argument("--cases", nargs="*")
    ap.add_argument("--runs", type=int, default=3)
    ap.add_argument("--model", default=os.environ.get("RELIANT_EVAL_MODEL", "bansos_codebuddy"))
    ap.add_argument("--base-url", default=os.environ.get("ANTHROPIC_BASE_URL", "http://127.0.0.1:20128/v1"))
    ap.add_argument("--api-key", default=os.environ.get("ANTHROPIC_AUTH_TOKEN", ""))
    ap.add_argument("--timeout", type=int, default=300)
    ap.add_argument("--pilot", action="store_true", help="one run of three representative cases")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv)

    root = Path.cwd()
    system = load_skill_context(root)
    print(f"system prompt: {len(system):,} chars (SKILL.md + {len(REFERENCE_ORDER)} references)")

    cases = args.cases or discover_cases(root)
    if args.pilot:
        cases = ["PG-001", "MY-006", "NEG-004"]
        args.runs = 1
        print(f"pilot mode: {cases}")

    out_root = root / "evals" / "results" / args.iteration
    print(f"{len(cases)} case(s) x {args.runs} run(s) -> {out_root}")
    print(f"model={args.model} base_url={args.base_url}")
    if not args.dry_run and not args.api_key:
        print("No API key. Set ANTHROPIC_AUTH_TOKEN or pass --api-key.", file=sys.stderr)
        return 2
    print()

    failures = 0
    for case_id in cases:
        case_dir = find_case_dir(root, case_id)
        user = build_user_prompt(case_dir, case_id)
        for run_index in range(1, args.runs + 1):
            label = f"run-{run_index}"
            dest = out_root / case_id / label
            dest.mkdir(parents=True, exist_ok=True)

            if args.dry_run:
                print(f"[{case_id}/{label}] would send {len(user):,} chars; no expected/ present")
                continue

            started = time.time()
            status, body = call_api(args.base_url, args.api_key, args.model, system, user, args.timeout)
            duration = round(time.time() - started, 1)

            if status == 200 and body.get("choices"):
                content = body["choices"][0]["message"]["content"]
                served = body.get("model", "unknown")
                usage = body.get("usage", {})
            else:
                content = f"API ERROR ({status}): {json.dumps(body)[:800]}"
                served = "n/a"
                usage = {}

            (dest / "report.md").write_text(content, encoding="utf-8")
            (dest / "run-meta.json").write_text(
                json.dumps(
                    {
                        "case_id": case_id,
                        "run": run_index,
                        "requested_model": args.model,
                        "served_model": served,
                        "base_url": args.base_url,
                        "date": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
                        "with_skill": True,
                        "mode": "single-turn, no tools, references preloaded",
                        "system_prompt_chars": len(system),
                        "user_prompt_chars": len(user),
                        "prompt_tokens": usage.get("prompt_tokens"),
                        "completion_tokens": usage.get("completion_tokens"),
                        "http_status": status,
                        "duration_s": duration,
                        "self_evaluated": True,
                        "independence_note": "fixtures authored by the same model family; not an independent evaluation",
                    },
                    indent=2,
                ),
                encoding="utf-8",
            )
            ok = status == 200 and len(content.strip()) > 0 and not content.startswith("API ERROR")
            failures += 0 if ok else 1
            print(f"[{case_id}/{label}] http={status} served={served} {duration}s {len(content)} chars{' FAIL' if not ok else ''}")

    if not args.dry_run:
        print(f"\nScore: python scripts/evaluate_reports.py --reports {out_root}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())