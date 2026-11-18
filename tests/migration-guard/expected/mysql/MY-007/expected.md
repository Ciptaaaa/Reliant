# MY-007 — Expected analysis

## Expected verdict

**APPROVED** — on MySQL 8.0.12+, adding a nullable column at the end of the table is supported with
`ALGORITHM=INSTANT` and `LOCK=NONE`.

## Findings

### F1 — `LOW` — `operational` — instant add-column is supported for this definition

`schema.sql` is MySQL 8.0.36, the column is added **at the end**, with `DEFAULT NULL` (no non-null
default to backfill), on a table without generated columns or partitioning. This is the supported
`ALGORITHM=INSTANT` add-column path (introduced in 8.0.12), so the migration is a metadata change: no
table rebuild, no copy. `LOCK=DEFAULT` is the required companion — INSTANT accepts only `LOCK=DEFAULT`.
A brief exclusive metadata lock is taken to register the column.

The explicit `ALGORITHM` clause is a good habit here: if this ran on 5.7 (no INSTANT) or the operation
became unsupported, MySQL would **reject** the statement instead of silently rebuilding.

**Evidence required:** `migration.sql`, `schema.sql` (server version, end position, nullable default).

**Mitigation:** none required. As a safeguard on managed databases, run with a short
`lock_wait_timeout` so a busy moment fails fast.

**Human approval required:** no.

### F2 — `INFO` — `no-risk-observed` — compatibility and data

No code references `nickname` (`application-usage.md`), so old code inserting without the column works.
Existing rows read `NULL` for the new column. No index, constraint, FK, or partition depends on it.

## Unknowns that must be reported

- The exact minimum version for INSTANT add-column is 8.0.12; if production were on 5.7, the same
  statement would fail (INSTANT unsupported) and the fallback would be a rebuild. The version **is**
  provided here, so this is not an unknown for this case; mention it as a version dependency.
- The later backfill (if any) is not part of this migration.

## Verdict reasoning

No finding above `LOW` and required evidence is present → `APPROVED`. Note MySQL 8.0.29+ changed what
INSTANT supports (adding columns at any position, `DROP COLUMN`), but this case does not depend on that:
an end-of-table nullable column is the original 8.0.12+ INSTANT case.
