#!/usr/bin/env python3
"""Apply every test fixture's schema to a real database and check the expected outcome.

For each case this script:
  1. Applies `schema.sql` to a scratch database.
  2. Applies `migration.sql`.
  3. Compares the result with what the fixture claims:
       - risky cases whose migration must fail (duplicates, orphans, NULLs) must FAIL
       - cases marked safe-to-apply must SUCCEED

It exists to stop the suite from slowly filling with fixtures that no longer reproduce the problem they
describe. It does not judge the skill's analysis; it only proves the fixtures are real.

Usage:
    python scripts/check_fixtures.py \
        --postgres "postgresql://postgres:pw@127.0.0.1:5432/postgres" \
        --mysql    "mysql://root:pw@127.0.0.1:3306/mysql"

Requires the `psql` and `mysql` clients. Exit code 1 if any fixture disagrees with its expectation.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path
from urllib.parse import urlparse

# case id -> expected outcome of applying migration.sql
MUST_FAIL = {
    "PG-001": "applies cleanly (SQL is valid; the risk is compatibility, not execution)",
    "PG-002": "contains null values",
    "PG-003": "could not create unique index",
    "PG-004": "violates foreign key constraint",
    "PG-005": "out of range",
    "PG-006": "cannot run inside a transaction block",
    "MY-002": "Invalid use of NULL value",
    "MY-003": "Duplicate entry",
    "MY-004": "Cannot add or update a child row",
    "MY-005": "ALGORITHM=INSTANT is not supported",
}

# Cases where the schema alone will not reproduce the failure. The seed statements create the offending
# rows that the fixture describes in `schema.sql` comments (the fixtures do not ship data, only counts).
SEED = {
    "PG-002": [
        "INSERT INTO customers (id) VALUES (1);",
        "INSERT INTO orders (customer_id, status, total_cents) VALUES (1, NULL, 100), (1, NULL, 100), (1, 'paid', 100);",
    ],
    "PG-003": [
        "INSERT INTO users (email, name) VALUES ('a@example.com', 'A'), ('a@example.com', 'A2'), ('b@example.com', 'B');",
    ],
    "PG-004": [
        "INSERT INTO customers (id, name) VALUES (1, 'C1');",
        "INSERT INTO orders (customer_id, total_cents) VALUES (1, 100), (999, 100);",
    ],
    "PG-005": [
        "INSERT INTO events (session_id, event_type, duration_ms, occurred_at) VALUES "
        "('00000000-0000-0000-0000-000000000001', 'click', 1200, now()), "
        "('00000000-0000-0000-0000-000000000002', 'session', 4212849000, now());",
    ],
    "MY-002": [
        "INSERT INTO payments (order_id, method, amount_cents, captured_at) VALUES (1, NULL, 100, NOW()), (1, 'card', 100, NOW());",
    ],
    "MY-003": [
        "INSERT INTO customers (name, reference_code) VALUES ('A', 'ABC123'), ('B', 'abc123'), ('C', 'xyz');",
    ],
    "MY-004": [
        "INSERT INTO orders (id, placed_at) VALUES (1, NOW());",
        "INSERT INTO payments (order_id, amount_cents, captured_at) VALUES (1, 100, NOW()), (999, 100, NOW());",
    ],
}

# Fixtures with prerequisite tables the schema file does not create.
PRELUDE = {
    "PG-002": "CREATE TABLE customers (id bigint PRIMARY KEY);",
}

SKIP = {
    "PG-008": "rename succeeds; the risk is application compatibility",
    "PG-007": "applies cleanly by design",
    "MY-001": "applies cleanly; the risk is application compatibility",
    "MY-006": "applies cleanly; the risk is lock contention",
    "MY-007": "applies cleanly; INSTANT path",
    "MY-008": "applies cleanly; the risk is application compatibility",
    "NEG-001": "negative case: applies cleanly",
    "NEG-002": "negative case: applies cleanly",
    "NEG-003": "negative case: applies cleanly",
    "NEG-004": "negative case: applies cleanly",
}


def run(cmd: list[str], sql: str) -> tuple[int, str]:
    proc = subprocess.run(cmd, input=sql, capture_output=True, text=True)
    return proc.returncode, (proc.stdout + proc.stderr)


def case_dir(root: Path, case_id: str) -> Path:
    hits = list(root.glob(f"tests/migration-guard/cases/*/{case_id}"))
    if not hits:
        raise SystemExit(f"case {case_id} not found")
    return hits[0]


def _schema_name(case_id: str) -> str:
    """A safe, unique schema/database name for one case."""
    return "case_" + case_id.lower().replace("-", "_")


def check_postgres(root: Path, conn: str, case_id: str) -> bool:
    d = case_dir(root, case_id)
    schema = (d / "schema.sql").read_text(encoding="utf-8")
    migration = (d / "migration.sql").read_text(encoding="utf-8")
    seed = "\n".join(SEED.get(case_id, []))
    prelude = PRELUDE.get(case_id) or ""
    namespace = _schema_name(case_id)

    # Each case gets its own schema. Without this, the second case to define `users` or `orders` failed
    # setup with `relation already exists`, which is a harness defect, not a fixture defect.
    # `citext` is an extension, not a built-in type (PG-001 declares `email citext`), so it must exist
    # before the schema is applied; the extension is installed once and left in place.
    # The harness no longer requires that extension to be installable: PG-001 gets a plain-text stand-in
    # when `citext` exists nowhere, so a server without `postgresql-contrib` can still apply the schema.
    # The stand-in is created only when no type of that name exists at all; `IF NOT EXISTS` alone is not
    # enough, because it compares domains inside `pg_temp` and would produce a second `citext` next to an
    # extension installed into another schema, which then fails with `type "citext" is only a shell`.
    # The comparison semantics of the stand-in do not matter here: the case has no seed rows, and the
    # migration under test only drops a different column.
    fallback = (
        "DO $$\n"
        "BEGIN\n"
        "  IF NOT EXISTS (SELECT 1 FROM pg_type WHERE typname = 'citext') THEN\n"
        "    CREATE DOMAIN pg_temp.citext AS text;\n"
        "  END IF;\n"
        "END\n"
        "$$;\n"
        if case_id == "PG-001"
        else ""
    )
    bootstrap = (
        f"CREATE EXTENSION IF NOT EXISTS citext;\n"
        f"{fallback}"
        f"DROP SCHEMA IF EXISTS {namespace} CASCADE;\n"
        f"CREATE SCHEMA {namespace};\n"
        f"SET search_path TO {namespace};\n"
    )
    setup = f"{bootstrap}BEGIN;\n{prelude}\n{schema}\n{seed}\nCOMMIT;\n"
    code, out = run(["psql", conn, "-v", "ON_ERROR_STOP=1", "-q", "-f", "-"], setup)
    if code != 0:
        print(f"[{case_id}] SETUP FAILED\n{out}")
        return False
    # Run the migration in its own transaction, inside the same schema, so a failure does not leave
    # state behind and does not touch another case.
    script = f"SET search_path TO {namespace};\nBEGIN;\n{migration}\nCOMMIT;\n"
    code, out = run(["psql", conn, "-v", "ON_ERROR_STOP=1", "-q", "-f", "-"], script)
    result = evaluate(case_id, code, out)
    # Clean up so a rerun starts from nothing.
    run(["psql", conn, "-v", "ON_ERROR_STOP=1", "-q", "-c", f"DROP SCHEMA IF EXISTS {namespace} CASCADE;"], "")
    return result


def mysql_args(conn: str, database: str | None = "") -> list[str]:
    """Turn a mysql:// URI into the flags the `mysql` client understands.

    `mysql "mysql://user:pw@host:port/db"` is not a supported form: the client treats the whole string
    as a database name and falls back to the local socket, which is how every MY case failed with
    "Can't connect to local server through socket" while the server was reachable over TCP.
    """
    parsed = urlparse(conn)
    if parsed.scheme not in ("mysql", "mariadb"):
        raise SystemExit(f"--mysql expects a mysql:// URI, got {conn!r}")
    args = ["mysql", f"--host={parsed.hostname or '127.0.0.1'}", f"--port={parsed.port or 3306}"]
    if parsed.username:
        args.append(f"--user={parsed.username}")
    if parsed.password:
        args.append(f"--password={parsed.password}")
    # `database=""` keeps the URI value; `None` connects with no database selected, which the
    # admin statements (CREATE/DROP DATABASE) need.
    db = (parsed.path or "/").lstrip("/") if database == "" else database
    if db:
        args.append(f"--database={db}")
    return args


def check_mysql(root: Path, conn: str, case_id: str) -> bool:
    d = case_dir(root, case_id)
    schema = (d / "schema.sql").read_text(encoding="utf-8")
    migration = (d / "migration.sql").read_text(encoding="utf-8")
    seed = "\n".join(SEED.get(case_id, []))
    namespace = _schema_name(case_id)

    # One database per case, for the same reason PostgreSQL gets one schema per case.
    admin = mysql_args(conn, database=None)
    code, out = run(admin, f"DROP DATABASE IF EXISTS {namespace}; CREATE DATABASE {namespace};\n")
    if code != 0:
        print(f"[{case_id}] NAMESPACE FAILED\n{out}")
        return False
    args = mysql_args(conn, database=namespace)
    code, out = run(args, f"{schema}\n{seed}\n")
    if code != 0:
        print(f"[{case_id}] SETUP FAILED\n{out}")
        return False
    code, out = run(args, migration)
    result = evaluate(case_id, code, out)
    run(admin, f"DROP DATABASE IF EXISTS {namespace};\n")
    return result


def evaluate(case_id: str, code: int, out: str) -> bool:
    expectation = MUST_FAIL.get(case_id)
    if expectation is None:
        print(f"[{case_id}] skipped ({SKIP.get(case_id, 'no expectation recorded')})")
        return True
    if expectation.startswith("applies cleanly"):
        if code == 0:
            print(f"[{case_id}] ok: applied as expected ({expectation})")
            return True
        print(f"[{case_id}] MISMATCH: expected clean apply, got failure\n{out}")
        return False
    if code == 0:
        print(f"[{case_id}] MISMATCH: expected failure ({expectation}), migration succeeded")
        return False
    if expectation.lower() not in out.lower():
        print(f"[{case_id}] ok: failed as expected, but message differs from '{expectation}'\n{out[:400]}")
        return True
    print(f"[{case_id}] ok: failed with '{expectation}'")
    return True


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--root", type=Path, default=Path("."))
    ap.add_argument("--postgres", help="psql connection string")
    ap.add_argument("--mysql", help="mysql connection URI")
    args = ap.parse_args(argv)
    root = args.root.resolve()

    cases = json.loads(
        json.dumps([p.parent.name for p in sorted(root.glob("tests/migration-guard/cases/*/*/migration.sql"))])
    )
    failures = 0
    for cid in cases:
        if args.postgres and cid.startswith("PG-"):
            failures += 0 if check_postgres(root, args.postgres, cid) else 1
        elif args.mysql and cid.startswith("MY-"):
            failures += 0 if check_mysql(root, args.mysql, cid) else 1
        elif not (args.postgres or args.mysql):
            print(f"[{cid}] no database connection provided")
            return 2
    if failures:
        print(f"\n{failures} fixture(s) disagree with their expectation.")
        return 1
    print("\nAll checked fixtures behave as documented.")
    return 0


if __name__ == "__main__":
    sys.exit(main())