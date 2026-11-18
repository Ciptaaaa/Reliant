# PG-005 — Expected analysis

## Expected verdict

**CONDITIONAL** — the type change is operationally heavy (table rewrite on 48M rows) and cannot
preserve 3,842 out-of-range values as written.

## Findings

### F1 — `HIGH` — `type-change` — narrowing `bigint` → `integer` loses 3,842 values

`schema.sql` records `max(duration_ms) = 4,212,849,000` and 3,842 rows above `2147483647`.
`bigint → integer` has an assignment cast, so `ALTER COLUMN duration_ms TYPE integer` converts
element-wise and fails at the first out-of-range row with `integer out of range` (SQLSTATE 22003). The
whole statement aborts atomically, so no data is corrupted — but the migration cannot be applied, and
any "fix" that silently saturates or rounds the values (e.g. `USING LEAST(duration_ms, 2147483647)`)
destroys them.

Severity is `HIGH`, not `BLOCKER`: the failure is loud, no existing data is destroyed by a failing
statement, and the affected rows can be re-handled. It becomes `BLOCKER` if the report proposes a
lossy `USING` without a decision record.

**Evidence required:** `schema.sql` (max value, 3,842 count), `migration.sql`,
`application-usage.md` (import_sessions can exceed 2^31).

**Mitigation:**

1. Decide the target type with the SDK owner: `bigint` (no change), `numeric`, or `integer` only after
   the importer's semantics change.
2. If integer is genuinely wanted, quarantine the out-of-range rows first and record what will happen
   to them (recompute, cap, drop) — a data decision, not an engineering one.
3. Then rewrite explicitly with `USING`, accepting that a rewrite of 48M rows is a planned operation,
   or use the add-new-column + dual-write + backfill + swap (expand and contract) so the rewrite
   happens in batches.

**Approval: accept-risk (F1)** — a human accepts the loss of the old values; a recovery plan and restore point are required.

### F2 — `HIGH` — `lock-and-rewrite` — full table and index rewrite under `ACCESS EXCLUSIVE`

Changing a column type normally rewrites the whole table and its indexes. On 48M rows this means:

- `ACCESS EXCLUSIVE` for the entire rewrite (no reads, no writes on `events`).
- Up to 2× the table's disk space temporarily.
- Every index on `events` rebuilt, including `events_session_id_idx`.
- PostgreSQL warns that rewrites are not MVCC-safe: transactions holding a snapshot from before the
  rewrite can see the table as empty after commit.

**Evidence required:** `migration.sql`, row count from `schema.sql`.

**Mitigation:** expand and contract — add `duration_ms_v2 integer`, backfill in batches, dual-write,
switch reads, drop the old column later. This avoids a single blocking rewrite entirely.

### F3 — `MEDIUM` — `application-compatibility` — statistics are lost after the type change

PostgreSQL drops the column's statistics after `ALTER COLUMN TYPE`; the dashboards and `aggregate.py`
queries can plan badly until `ANALYZE events` runs. Plan an `ANALYZE` immediately after the change.

### F4 — `INFO` — `no-risk-observed` — reads are type-agnostic

The serializer returns a Python int either way and the dashboard casts to `numeric`, so no read path
breaks from the type change itself.

## Unknowns that must be reported

- Whether the mobile SDK can emit values above 2^31 after the semver-locked change (the SDK repo was
  not inspected).
- Disk headroom for a 2× rewrite is not stated.

## Verdict reasoning

No proven data destruction, but a likely-failing data change plus a blocking 48M-row rewrite →
`HIGH` → `CONDITIONAL`.
