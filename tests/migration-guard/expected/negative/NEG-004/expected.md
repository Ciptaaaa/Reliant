# NEG-004 — Expected analysis

## Expected verdict

**APPROVED** — on PostgreSQL 16, a new column with a constant default is a metadata-only change.

## Findings

### F1 — `LOW` — `operational` — non-volatile default is metadata-only

`ALTER TABLE invoices ADD COLUMN currency varchar(3) NOT NULL DEFAULT 'USD'` on PostgreSQL 16. The
`ALTER TABLE` notes state that with `ADD COLUMN` and a **non-volatile** default, the value is evaluated
at statement time and stored in the table's metadata, so "the ALTER TABLE [is] very fast even on large
tables … no rewrite of the table [is] required". The same metadata default is returned for existing
rows, so no backfill is needed. The `ACCESS EXCLUSIVE` lock is momentary.

**Evidence required:** `migration.sql`, `context.md` (PG 16), `schema.sql`.

**Mitigation:** none. Version dependency worth stating: on PostgreSQL 10 and older the same statement
rewrites the table, which would make it `HIGH` on a large table.

### F2 — `INFO` — `no-risk-observed` — compatibility

The column is added with a default and is `NOT NULL`, so inserts from the current application version
(which do not mention `currency`) succeed and receive `'USD'`. Existing rows read `'USD'`. No index or
constraint is added. The literal `'USD'` is non-volatile (not `now()`/`random()`), which is what makes the
fast path apply.

## Unknowns that must be reported

- None material. The check constraint or backfill for non-USD invoices is a later change.

## Verdict reasoning

`LOW` only, evidence present → `APPROVED`. This looks like the "NOT NULL on existing table" pattern that
is risky, but it is a *new* column with a metadata default; flagging a rewrite or scan is a false
positive.

## This is a negative case

Any `HIGH`+ finding, a "rewrites the table" claim, a backfill recommendation, or a non-`APPROVED`
verdict counts as a false positive / verdict miss (the version caveat above is a note, not a finding).
