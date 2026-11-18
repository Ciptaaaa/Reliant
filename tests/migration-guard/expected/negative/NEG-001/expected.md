# NEG-001 — Expected analysis

## Expected verdict

**APPROVED** — a single nullable column with no default on PostgreSQL 16, with no other change.

## Findings

### F1 — `LOW` — `operational` — metadata-only add

`ALTER TABLE signups ADD COLUMN referral_source varchar(64)` adds a nullable column with no default. On
PostgreSQL 11+, this is a catalog-only change with no table rewrite ("If no column constraints are
specified, NULL is used as the DEFAULT … In neither case is a rewrite of the table required"). The
`ACCESS EXCLUSIVE` lock is momentary. The table is small (~90k rows) and the statement is identical in
cost on any size.

**Evidence required:** `migration.sql`, `schema.sql`.

**Mitigation:** none.

### F2 — `INFO` — `no-risk-observed` — nothing else touched

No column altered or dropped, no constraint or index added, no backfill, no application code reads the
column yet. Old and new application versions both work.

**Approval: none** — every step in this migration is reversible without a backup.

## Unknowns that must be reported

- None material. The later use of the column is not part of this migration.

## Verdict reasoning

Highest finding is `LOW`; evidence present → `APPROVED`. A report that recommends a backfill, or
claims a rewrite, is a false positive.

## This is a negative case

If the report contains any `HIGH`+ finding or a `BLOCKED`/`CONDITIONAL` verdict, count it as a false
positive / verdict miss.
