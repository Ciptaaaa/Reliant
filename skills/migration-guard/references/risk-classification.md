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

Assign `HIGH` when at least one of:

- Likely (not proven) data loss, for example a narrowing type change with no measured range, or a
  `NOT NULL` backfill whose row count is unknown.
- A table rewrite on a table whose size is unknown or reported as large, or a lock that blocks writes
  for a non-trivial period.
- A deploy-order incompatibility: the change only works if the old application version is already
  fully retired and that is not the case.
- A partial-failure mode: multi-statement MySQL DDL with no safe resume, or an invalid index left
  behind by a failed build.
- A failed statement that leaves the schema in a mixed state with no documented recovery.

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
   referential integrity, value range, charset)? Is that property verified? Not verified → at least
   `HIGH`. Verified violated → `BLOCKER`.
4. **Check operational cost.** Rewrite? Blocking lock? Backfill? Use the engine reference to decide;
   unknown table size → `HIGH`.
5. **Check recoverability.** Is the change reversible in schema **and** data? Schema reversible but
   data lost → still at least `HIGH`; completely irreversible without backup → `BLOCKER`.
6. **Cap by evidence quality.** If a step depends on information you do not have, do not report a
   severity lower than the severity of the unknown. "Unknown" is not "safe".

## 3. Evidence requirements per level

| Level | Evidence required |
|---|---|
| `BLOCKER` | A citation or explicit data fact that proves the consequence (code reference, duplicate count, orphan count, `IS NULL` count, absence of backup) |
| `HIGH` | Either a concrete mechanism (rewrite/lock) plus a reason it is costly, or an unverified data assumption that the migration relies on |
| `MEDIUM` | A concrete mitigation that is missing or unspecified |
| `LOW` | The schema state and the engine support that make the operation cheap |
| `INFO` | A description of scope |

A finding without a citation is not allowed at any level. If you cannot cite, the finding becomes an
**Unknown**.

## 4. Verdict rules

```
if any finding.severity == BLOCKER:            verdict = BLOCKED
elif any finding.severity in (HIGH, MEDIUM):   verdict = CONDITIONAL
elif unknown_blocks_evidence:                  verdict = CONDITIONAL
else:                                          verdict = APPROVED
```

`unknown_blocks_evidence` is true when an unknown could hide a `BLOCKER`, for example:

- The engine version is unknown and the proposed syntax is version-dependent.
- Row counts are unknown and the migration assumes a data property (`NOT NULL`, `UNIQUE`, `FK`).
- Whether the old application version still runs is unknown and the migration renames or drops.
- Whether a backup exists is unknown and the migration is destructive.

Even a purely `LOW` migration is `CONDITIONAL` when the engine version is unknown if that version
determines whether the operation rewrites or locks, because the alternative is an unprovable
`APPROVED`.

## 5. Handling incomplete information

Never fill gaps with plausible numbers. Use these statements exactly:

- **Unknown row count**: "Table size not provided. If the table is large, `<operation>` becomes
  `HIGH` because of `<mechanism>`. Verify with `SELECT count(*)` before proceeding."
- **Unknown engine version**: "Engine version not provided. `<operation>` behaves differently on
  versions `<a>` and `<b>`; the risk cannot be closed without the version."
- **Unknown application compatibility**: "Could not confirm whether the previous application version
  still runs during the deploy window. Renames/drops require old code retired first."
- **Absence of evidence**: "Searched `<paths>` for `<identifier>`: no references. This does not cover
  `<external surfaces>`."

The Unknowns section of the report must list every such gap.

## 6. What must not happen

- `APPROVED` while a required piece of evidence is missing.
- A `LOW` severity justified by "small table" when the size was not provided.
- Treating `IF EXISTS` / `IF NOT EXISTS` as a mitigation.
- Treating a pending task ("we will backfill later") as a mitigation unless the task is specified,
  ordered, and gated.
- Counting an `INFO` finding as risk.
- Silent downgrade of a severity because the recommended fix makes the operation safe. Severity
  describes the migration *as written*.
