# Change Impact Taxonomy

Normative reference for every Reliant skill. Phase 1 (`migration-guard`) is the first consumer; Phase 2
(`api-contract-guard`), Phase 3 (`authorization-guard`), Phase 4 (`behavior-preservation`) and Phase 5
(`release-safety`) must use these definitions unchanged, so results are comparable across skills.

When this file and any other Reliant document disagree, **this file wins** for the meaning of the
terms. When this file and engine documentation disagree about engine behaviour, the documentation
wins and this file is fixed.

## 1. Why these terms are separated

Three different questions get collapsed into one word — "destructive" — and that collapse is the main
source of inconsistency found in iterations 1–2:

| Question | Term | Consequence if answered wrongly |
|---|---|---|
| Can I put the previous state back? | **Reversibility** | An agent asks a human to accept data loss that does not happen, or skips approval for data loss that does |
| Is stored information lost forever? | **Data loss** | A safe, failing constraint is treated like a `DROP COLUMN` |
| Will this hurt while it runs or deploys? | **Operational risk** | A window/backfill requirement is never gated, because it is not a "finding" |

They are **independent**. `ADD UNIQUE` on duplicate data is: reversible, no data loss, low operational
risk, and still a `BLOCKER` because it cannot succeed. `DROP COLUMN` on a nullable column no code
reads is: irreversible, no *meaningful* loss, near-zero operational risk, and still `HIGH` because a
recovery is only from backup.

## 2. Vocabulary

### 2.1 Change

One migration statement (Phase 1) or one externally visible contract change (Phase 2+). A case may
contain several changes.

### 2.2 Effect on stored data

| Term | Definition | Examples |
|---|---|---|
| **Additive** | Creates structure or rows; touches no existing value | `ADD COLUMN` nullable, `CREATE TABLE`, `CREATE INDEX` |
| **Restrictive** | Adds a constraint that existing data may violate; refuses to apply if it does | `ADD UNIQUE`, `ADD FK`, `SET NOT NULL`, `ADD CHECK` |
| **Narrowing** | Changes a type or contract to accept a subset of current values | `bigint → integer`, `varchar(255) → varchar(64)`, `varchar → enum` |
| **Transformative** | Rewrites every value, even if the type is unchanged | `USING round(x)`, case normalisation, unit conversion |
| **Removing** | Deletes structure together with the values in it | `DROP COLUMN`, `DROP TABLE` |

**Only `Transformative` and `Removing` are candidates for data loss.** `Restrictive` and `Narrowing`
never lose data by themselves: they either apply or they abort. See §3.

### 2.3 Outcome of applying the change

A change has exactly one of three outcomes. This distinction decides reversibility more often than the
operation name does.

| Outcome | Definition | Example |
|---|---|---|
| **Applied** | The change commits | `DROP COLUMN phone` succeeds |
| **Refused** | The engine rejects it and the transaction rolls back; no row was modified | `ADD UNIQUE` on duplicates, `ADD FK` on orphans, `SET NOT NULL` with nulls, `bigint → integer` with out-of-range values |
| **Partial** | Some statements committed and some did not, with no automatic recovery | MySQL multi-statement DDL (implicit commit), a failed `CREATE INDEX CONCURRENTLY` leaving an `INVALID` index |

**A refused change is always reversible and never loses data.** It is still a `BLOCKER` when the
refusal is certain, because the deploy fails. Severity measures *how bad shipping it is*; reversibility
measures *whether the old state can be restored*.

### 2.4 Reversibility

The question is: **after this change has reached its stated outcome, can the previous state be
restored without a backup?**

| Class | Definition | Test |
|---|---|---|
| **Reversible** | The change can be undone with a statement, and every value the change wrote can be recomputed or was never overwritten | Drop the column you just added; re-add the constraint you just dropped; undo a rename; the change was refused |
| **Irreversible** | Undoing the structure does not restore the values; only a backup or restore point does | `DROP COLUMN` with values; `DROP TABLE` with rows; an `UPDATE` that overwrites the only copy; a truncating narrowing conversion |

Two clarifications that decide most real cases:

- **A rename is reversible.** `RENAME` moves the same values under a new name; renaming back restores
  everything. A rename is *breaking* (deploy order, §2.5) but *not* irreversible.
- **A narrowing conversion that aborts is reversible.** If `bigint → integer` fails on out-of-range
  rows, PostgreSQL aborts the statement atomically and no value changed (see `references/postgres.md`
  §6). Only a conversion that succeeds *and truncates or rounds* is irreversible.

### 2.5 Operational risk

Risk incurred while the change runs or while it is being deployed. It never causes data loss, and it is
not a reason to ask a human to accept one — but it does need a **schedule**.

| Kind | Definition | Gated by |
|---|---|---|
| **Lock / blocking** | Holds a lock that blocks readers or writers, or queues behind a long transaction (lock pile-up) | A window, a `lock_timeout`, or a rewrite of the statement |
| **Rewrite / copy** | Rewrites the table or copies it, costing I/O and disk | A window, or a shadow-column pattern |
| **Backfill** | Must update existing rows in batches before or after the DDL | Batch size, idempotent predicate, abort conditions |
| **Deploy-order** | Fails or misbehaves only because two application versions run at once | Expand-and-contract, or a stop-the-world window |
| **Partial-failure** | A failure can leave a mixed state (invalid index, half-applied multi-statement DDL) | A resume/cleanup step before retrying |

**Deploy-order incompatibility is operational risk, not data loss.** A `RENAME` that breaks old
replicas is `HIGH` operational risk with `schedule`. It creates no data to accept.

### 2.6 Deploy-order compatibility

Separate from reversibility, and asked separately:

| Question | Answer | Consequence |
|---|---|---|
| Does the **old** application still work after this change? | yes / no / unknown | `no` → at least `HIGH`, mitigate with expand-and-contract |
| Does the **new** application work *before* this change runs? | yes / no / unknown | `no` → the code and the migration must ship together or in a fixed order |

## 3. The decision procedure

Apply in this order. Each step depends on the previous one.

```
1. What is the effect on stored data?            §2.2  → additive | restrictive | narrowing | transformative | removing
2. What is the outcome if it runs as written?    §2.3  → applied | refused | partial
3. Is the change reversible?                     §2.4  → yes if refused or additive or rename; no if it removed/overwrote values
4. Is data lost?                                 §2.4  → only if outcome is applied AND effect is transformative or removing
5. What operational risk does it carry?          §2.5  → lock | rewrite | backfill | deploy-order | partial-failure
6. What severity?                                risk-classification.md §2
7. What approval class?                          §4 below
```

### Data loss is a consequence of (outcome, effect), not of the operation name

| Effect \ Outcome | Applied | Refused | Partial |
|---|---|---|---|
| Additive | no loss | no loss | no loss |
| Restrictive | no loss | **no loss** | no loss |
| Narrowing | **loss only if it truncates/rounds** | **no loss** (atomic abort) | possible loss from the committed prefix |
| Transformative | **loss** (previous values overwritten) | no loss | loss from the committed prefix |
| Removing | **loss** (values deleted) | no loss | loss from the committed prefix |

A `PARTIAL` outcome is the only case where a `Restrictive` change can lose data, and only because an
earlier statement in the same file committed.

## 4. Approval classes

An approval to *accept risk* is required only when data is lost irreversibly **or** when the change
overwrites values that cannot be recomputed.

| Class | When it applies | What the report must contain |
|---|---|---|
| `none` | The change is reversible, or the outcome is `refused`, or it is additive and nothing has written to it | A sentence saying why no human is needed |
| `schedule` | The change is reversible but carries operational cost requiring a window, a batch plan, or a deploy sequence | The window, the throttle, and the abort conditions |
| `accept-risk` | Data is lost irreversibly, or values are overwritten with no recomputation and no backup path already agreed | The restore point, the recovery plan, and a named human approval |

Rules that follow directly:

- **Deploy-order incompatibility alone never needs `accept-risk`.** It needs `schedule`.
- **A refused change never needs `accept-risk`.** It needs the repair that makes it applicable.
- **A `BLOCKER` may carry `none`.** Severity and approval answer different questions: `ADD UNIQUE` on
  duplicates is a `BLOCKER` that requires no one to accept data loss.
- **`accept-risk` without a restore point is itself a `BLOCKER`.** ("This will destroy data and there
  is no way back" is the state the skill exists to stop.)

## 5. Worked examples from the Phase 1 suite

These are the cases that forced this taxonomy to exist. Each shows the three questions answered
separately.

| Case | Effect | Outcome | Reversible | Data lost | Operational | Verdict | Approval |
|---|---|---|---|---|---|---|---|
| PG-001, MY-001 `DROP COLUMN` used by code | removing | applied | **no** | **yes** | lock | `BLOCKED` | `accept-risk` |
| PG-003, MY-003 `ADD UNIQUE` on duplicates | restrictive | **refused** | **yes** | no | lock (scan) + deploy-order | `BLOCKED` (cannot ship) | **`none`** for the failure; `schedule` for the index build |
| PG-004, MY-004 `ADD FK` on orphans | restrictive | **refused** | **yes** | no | lock + deploy-order (delete path) | `BLOCKED` | **`none`** for the failure; `schedule` for the rest |
| PG-005 `bigint → integer`, out of range | narrowing | **refused** | **yes** | no | rewrite | `CONDITIONAL` | **`none`** for the failure; `schedule` for the rewrite |
| PG-008, MY-008 `RENAME COLUMN` | additive + rename | **applied** | **yes** | no | deploy-order | `CONDITIONAL` | **`schedule`** |
| PG-002 `SET NOT NULL` with nulls | restrictive | refused | yes | no | scan + deploy-order | `CONDITIONAL` | `none` / `schedule` |
| PG-006 `CREATE INDEX CONCURRENTLY` in a txn | additive | refused | yes | no | partial-failure | `CONDITIONAL` | `none` |
| PG-007 `ADD COLUMN` nullable | additive | applied | yes | no | none | `APPROVED` | `none` |

Note the two that used to be misclassified: the `RENAME` cases are **reversible** (the values move,
they are not lost), and the constraint/type cases are **refused and reversible** (nothing was written).

## 6. Terms this taxonomy forbids

- **"Destructive"** unqualified. It confuses `removing` with `restrictive`. Say `Removing` (data deleted)
  or `Refused` (nothing applied). `destructive` survives only as the deprecated JSON field name in
  `tests/migration-guard/expected-schema.json`; `irreversible` replaces it.
- **"Not safe"** as an approval decision. State which of the three questions failed: reversibility,
  data loss, or operational risk.
- **"Data loss" for a failed constraint.** A refused change wrote nothing. Call it a deploy failure.
- **"Rollback"** for a schema revert that does not restore values. Say **schema rollback** and
  **data recovery** separately.

## 7. Applying this to later phases

Each later skill reuses §2.2–§2.5 with its own "stored data" substitute:

| Phase | "Stored data" is | "Refused" is | "Removing" is |
|---|---|---|---|
| 2 API Contract Guard | request/response payloads consumers depend on | the deploy rejects the contract, or a consumer breaks at runtime | a field/endpoint deleted while a consumer still uses it |
| 3 Authorization Guard | access decisions and audit records | the policy change is rejected by a test | a permission removed, or an access path opened |
| 4 Behavior Preservation | business rules and their observable outputs | the change contradicts an existing rule | a rule silently dropped |
| 5 Release Safety | the union of the above | any guard returns `BLOCKED` | any irreversible step without a recovery plan |

The approval classes in §4 carry over unchanged. That is the point of writing them down once.
