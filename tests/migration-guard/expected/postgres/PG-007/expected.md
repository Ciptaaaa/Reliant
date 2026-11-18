# PG-007 — Expected analysis

## Expected verdict

**APPROVED** — a nullable column with no default is a metadata-only change on PostgreSQL 11+.

## Findings

### F1 — `LOW` — `operational` — additive column, metadata-only change

`ALTER TABLE users ADD COLUMN locale varchar(10)` on PostgreSQL 15 adds a column with no default.
Per the `ALTER TABLE` notes, "If no column constraints are specified, NULL is used as the DEFAULT …
In neither case is a rewrite of the table required." The statement only updates the catalog, so it is
fast regardless of the 310k rows.

It still takes `ACCESS EXCLUSIVE` for that instant. With a short `lock_timeout` and no long-running
transactions on `users`, the practical impact is negligible.

**Evidence required:** `migration.sql`, `schema.sql` (engine version and absence of the column).

**Mitigation:** none required. Optional hardening: `SET lock_timeout = '3s'` in the migration session.

**Human approval required:** no.

### F2 — `LOW` — `application-compatibility` — old and new versions both work

Existing code does not reference `locale` (`application-usage.md`), and the column is nullable with no
default, so inserts from the current application version — which does not know about the column —
continue to succeed. Rolling deploy is safe.

**Evidence required:** `application-usage.md`.

### F3 — `INFO` — `no-risk-observed` — nothing destructive

No column is dropped, altered, or made stricter. No index or constraint is added that could fail on
existing data. No data is rewritten.

## Unknowns that must be reported

- The size of the later backfill (if the project chooses to default `locale` for existing users) is not
  part of this migration; when that happens, it becomes a batched backfill (`MEDIUM`).

## Verdict reasoning

No finding above `LOW`; required evidence is present; scope is stated → `APPROVED`. This is the
approved-with-evidence case: the report must state that `APPROVED` is scoped to what was inspected.
