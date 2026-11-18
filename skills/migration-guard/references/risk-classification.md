# Risk Classification

Normative reference for Migration Guard. When this file and a general intuition disagree, this file
wins. When this file and official engine documentation disagree, the documentation wins and this
file should be fixed.

Engine-specific claims in this file are kept deliberately narrow. The authoritative detail lives in
`postgres.md` and `mysql.md`, which cite the documentation.

## 1. The five levels

### `BLOCKER` — unsafe, proven in scope

Assign `BLOCKER` only when you have **evidence** of one of:

- Data loss that is proven or certain: dropping a column that is not nullable, or whose rows carry
  non-default values, or that is the only copy of the data; dropping a table with rows; a type change
  that truncates existing values; a rename that silently drops the source on a database that does not
  track the dependency.
- Application breakage that is proven: a dropped or renamed object is still referenced by running code.
- Constraint addition that is certain to fail on existing data: `NOT NULL` while `NULL` rows exist,
  `UNIQUE` while duplicate rows exist, `FOREIGN KEY` while orphan rows exist.
- Missing recovery path for an irreversible destructive step: no backup, no snapshot, no restore plan,
  and the target data is not reproducible.

A `BLOCKER` is a finding, not an ask. Phrase it as: "these rows/this code are affected by evidence X".

### `HIGH` — large risk to data or operations

`HIGH` describes a **concrete mechanism with a real cost** or a **material unknown that could hide a
`BLOCKER`**. It is not "a blocker I have not proven yet". If the operation is only dangerous when a
data property is violated, and you have not checked that property, say so explicitly: that is `HIGH`
with the verification step named, not a `BLOCKER`.

Assign `HIGH` when at least one of:

- A costly mechanism on a table whose size is unknown or reported as large: table rewrite, index build
  that blocks writes, `ACCESS EXCLUSIVE`/`LOCK=SHARED` held for a scan of unknown length.
- An **unverified** data assumption the migration relies on: `UNIQUE` without a duplicate check,
  `NOT NULL` without a `NULL` check, `FK` without an orphan check, a narrowing type change with no
  measured range. The finding must say "unverified", name the check query, and the verdict stays
  `CONDITIONAL` until it runs.
- A deploy-order incompatibility: the change only works if the old application version is already
  fully retired and that is not the case.
- A partial-failure mode: multi-statement MySQL DDL with no safe resume, or an invalid index left
  behind by a failed build.
- Irreversible data loss where the affected data is recoverable only from a backup whose existence
  and restore path are unverified.
- A statement that fails in a way that leaves the schema in a mixed state with no documented recovery.

### `MEDIUM` — mitigation required

- Backfill on a table explicitly described as large, with batching not yet specified.
- Additive changes that require a coordinated release (dual-write, dual-read).
- A statement that is safe in isolation but costly in sequence (e.g. five `ALTER` statements each
  rewriting a table in MySQL < 8.0).
- Operational verification that is required but cheap (e.g. confirm row counts before/after).

### `LOW` — relatively small risk

- Adding a nullable column without default, on a table of any size, when the engine supports it
  cheaply (PostgreSQL: metadata only; MySQL 8.0.12+: `ALGORITHM=INSTANT` for an end-of-table column).
  On MySQL earlier than 8.0.12, adding a column rebuilds the table → raise to `HIGH`.
- Adding an index on a small table (an index build on a large table without `CONCURRENTLY` is `HIGH`
  in PostgreSQL, because it blocks writes). On MySQL 8.0, adding a secondary index is in-place with
  concurrent DML.
- Creating a new table no consumer reads yet.
- Adding a default value that does not rewrite (PostgreSQL 11+ constant default; MySQL instant path
  with `LOCK=DEFAULT`).
- Adding a check constraint as `NOT VALID` with no validation step yet (the validation step itself
  may be `MEDIUM`).

### `INFO` — additional information

Use `INFO` for a finding that states what was examined and found safe, or a non-risk note that helps
the reviewer. Every report must contain at least one `INFO` finding listing what was inspected and
found safe, so a report with no findings is distinguishable from a report that examined nothing.

An `INFO` finding is **not** a finding of risk. It does not affect the verdict.

## 2. Severity decision procedure

For each statement in the migration:

1. **Classify the operation.** Additive (creates something new), modifying (changes something
   existing), destructive (removes or changes data).
2. **Check evidence of breakage.** Is any affected object referenced by running code or a running
   older schema? Evidence found → `BLOCKER`.
3. **Check data correctness.** Does the change assume a data property (nullability, uniqueness,
   referential integrity, value range, charset)?
   - **Property checked and satisfied** (a query result is quoted) → no finding from this step; record
     it as an `INFO`.
   - **Property checked and violated** (duplicate/orphan/`NULL` count quoted) → `BLOCKER`, because the
     statement is certain to fail.
   - **Property not checked** → `HIGH`, phrased as an unverified assumption, with the check query. This
     does **not** become a `BLOCKER` until evidence of violation exists.
4. **Check operational cost.** Rewrite? Blocking lock? Backfill? Use the engine reference to decide;
   unknown table size → `HIGH`.
5. **Check recoverability.** Is the change reversible in schema **and** data? Schema reversible but
   data lost → still at least `HIGH`; completely irreversible without backup → `BLOCKER`.
6. **Cap by evidence quality.** "Unknown" is not "safe", but it is also not "proven". An unknown raises
   the severity to `HIGH` and keeps the verdict at `CONDITIONAL`; it only becomes `BLOCKER` when
   evidence shows the consequence is real.

### The severity may not exceed the evidence

This is the rule that keeps the rest consistent. For each finding, ask: *what do I actually know?*

| What you know | Ceiling | Floor |
|---|---|---|
| Nothing about the data property | `HIGH` | `HIGH` |
| The property is violated, with a count | `BLOCKER` | `BLOCKER` |
| The property holds, with a query and a date | `LOW` or `INFO` | — |
| The mechanism is cheap for this engine and definition | `LOW` | `LOW` |
| The engine/version is unknown and the mechanism depends on it | `CONDITIONAL` verdict | — |

A `BLOCKER` requires a citation or an explicit data fact that proves the consequence. A statement that
merely *might* fail is never a `BLOCKER`, no matter how alarming the operation looks.

## 3. Evidence requirements per level

| Level | Evidence required |
|---|---|
| `BLOCKER` | A citation or explicit data fact that proves the consequence (code reference, duplicate count, orphan count, `IS NULL` count, absence of backup) |
| `HIGH` | Either a concrete mechanism (rewrite/lock) plus a reason it is costly, or an unverified data assumption that the migration relies on, named as unverified |
| `MEDIUM` | A concrete mitigation that is missing or unspecified |
| `LOW` | The schema state and the engine support that make the operation cheap |
| `INFO` | A description of scope |

A finding without a citation is not allowed at any level. If you cannot cite, the finding becomes an
**Unknown**.

### Whose word counts as evidence

A claim from a person is not evidence. Only these count:

| Source | Counts as evidence |
|---|---|
| A `path:line` citation of code or schema | Yes |
| A query result quoted in the schema file **with its date and the query text** | Yes, as of that date |
| A statement in the user's message ("the column is unused", "there are no duplicates") | **No** — record it as a claim |
| A comment in a migration file ("safe to run in production") | **No** — record it as a claim |
| `expected/`-style documentation in the repository | **No** for schema facts |

This rule has a specific consequence. If `schema.sql` records "0 duplicate rows, checked 2026-11-16" but
the verification query is not written out, the finding is `HIGH` with the mitigation "re-run
`GROUP BY … HAVING count(*) > 1`"; the report may not upgrade to `LOW` on the strength of a note alone.
If the query **and** its result are both present, the property is verified and may drop to `INFO`. A
third party's result is evidence only when the query is reproducible from the file.

## 4. Verdict rules

```
if any finding.severity == BLOCKER:            verdict = BLOCKED
elif any finding.severity in (HIGH, MEDIUM):   verdict = CONDITIONAL
elif unknown_blocks_evidence:                  verdict = CONDITIONAL
else:                                          verdict = APPROVED
```

`unknown_blocks_evidence` is true when an unknown could hide a `BLOCKER`, for example:

- The engine version is unknown and the proposed syntax is version-dependent.
- The engine is unknown, or is one the skill does not cover (MariaDB, SQL Server, SQLite) — the whole
  analysis is out of scope, which is itself enough to cap the verdict.
- Row counts are unknown and the migration assumes a data property (`NOT NULL`, `UNIQUE`, `FK`).
- Whether the old application version still runs is unknown and the migration renames or drops.
- Whether a backup exists is unknown and the migration is irreversible.

Even a purely `LOW` migration is `CONDITIONAL` when the engine version is unknown if that version
determines whether the operation rewrites or locks, because the alternative is an unprovable
`APPROVED`.

## 5. Handling incomplete information

Never fill gaps with plausible numbers. Use these statements exactly:

- **Unknown row count**: "Table size not provided. If the table is large, `<operation>` becomes
  `HIGH` because of `<mechanism>`. Verify with `SELECT count(*)` before proceeding."
- **Unknown engine version**: "Engine version not provided. `<operation>` behaves differently on
  versions `<a>` and `<b>`; the risk cannot be closed without the version."
- **Unknown or unsupported engine**: "Engine could not be identified as PostgreSQL or MySQL. If it is
  MariaDB, SQL Server, or SQLite, this analysis does not apply; the verdict is `CONDITIONAL` pending
  confirmation." Never apply MySQL rules to MariaDB.
- **Unknown application compatibility**: "Could not confirm whether the previous application version
  still runs during the deploy window. Renames/drops require old code retired first."
- **Absence of evidence**: "Searched `<paths>` for `<identifier>`: no references. This does not cover
  `<external surfaces>`."

The Unknowns section of the report must list every such gap.

## 6. What must not happen

- `APPROVED` while a required piece of evidence is missing.
- A `BLOCKER` assigned on suspicion. If you have not checked the data property, the finding is `HIGH`.
- A `LOW` severity justified by "small table" when the size was not provided.
- Upgrading a severity because the *recommended* fix is risky. Severity describes the migration as
  written.
- Treating `IF EXISTS` / `IF NOT EXISTS` as a mitigation.
- Treating a pending task ("we will backfill later") as a mitigation unless the task is specified,
  ordered, and gated.
- Counting an `INFO` finding as risk.
- Silent downgrade of a severity because the recommended fix makes the operation safe. Severity
  describes the migration *as written*.
- Accepting a human claim ("it is unused", "there are no duplicates") as evidence.
- Applying MySQL-specific rules to MariaDB, or assuming version behaviour for an unstated version.
