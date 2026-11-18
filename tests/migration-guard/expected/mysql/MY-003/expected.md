# MY-003 — Expected analysis

## Expected verdict

**BLOCKED** — duplicates still exist, so the unique index cannot be created; the online-DDL clause
does not help with a data problem.

## Findings

### F1 — `BLOCKER` — `unique-constraint` — unique index fails on existing duplicates

`schema.sql` records 2,918 exact duplicate groups and 3,067 case-insensitive duplicates. Because the
column uses `utf8mb4_0900_ai_ci` (case-insensitive), the unique index compares case-insensitively and
fails with `Duplicate entry 'abc123-1' for key 'customers.customers_reference_code_unique'` on the
first of 3,067 offending groups. The migration cannot be applied as written.

**Evidence required:** `schema.sql` (duplicate counts, collation), `migration.sql`.

**Mitigation:**

1. Run the dedupe command and record how many rows are merged/deleted (a data change, requires
   approval). Confirm `SELECT reference_code FROM customers GROUP BY reference_code HAVING COUNT(*)>1`
   returns 0.
2. Fix `ImportCustomers.php:44` to normalise case on ingest, otherwise new lowercase duplicates
   reintroduce the problem (and now hit the unique index and abort the import).
3. Build the index online:
   `ALTER TABLE customers ADD UNIQUE KEY customers_reference_code_unique (reference_code), ALGORITHM=INPLACE, LOCK=DEFAULT;`
   (see F2 for the lock level).

Decide the intended semantics first: if the code is case-insensitive by design, keep the `_ai_ci`
collation and dedupe case-insensitively; if it is case-sensitive, use a `utf8mb4_bin` (or
`utf8mb4_0900_as_cs`) collation on the column and dedupe exactly.

**Approval: none (F1)** — the unique index build is refused and the DDL rolls back, so no data is lost. Resolving the duplicates is the separate data change that needs a human decision.

### F2 — `HIGH` — `index-build` — the online-DDL clause does not make the data problem go away

A unique index build is an in-place operation that supports concurrent DML on MySQL 8.0, so the
`ALGORITHM=INPLACE` intent is reasonable. What the clause cannot do is skip the duplicate check: the
build scans the table and fails the moment the first duplicate is found. Naming the algorithm is still
valuable — if the server cannot honour `INPLACE` it errors instead of silently copying the table — but
the migration's stated purpose ("no downtime") is unachievable while 3,067 groups violate the
constraint. Do not claim a specific lock level for a unique build beyond what the server reports;
verify with `SHOW WARNINGS` on staging.

**Evidence required:** `migration.sql` (the `ALGORITHM=INPLACE` clause).

**Mitigation:** dedupe first (F1), then run the build with an explicit algorithm and check on staging
that MySQL accepts it; monitor `performance_schema.metadata_locks` during the build.

### F3 — `MEDIUM` — `application-compatibility` — import writes lowercase references

`ImportCustomers.php:44` preserves CSV case. Once the unique index exists, a CSV containing `abc123`
after `ABC123` already exists will abort the weekly import with `Duplicate entry`. Normalise case in the
importer, and decide how to handle existing partner records that collide.

**Evidence required:** `application-usage.md` (ImportCustomers).

### F4 — `INFO` — `no-risk-observed` — lookups remain correct

Application lookups (`CustomerController.php:31`) use an equality match and the column is already
case-insensitive, so deduplicating case variants does not change lookup behaviour.

## Unknowns that must be reported

- Whether the partner CSV can contain case-variant duplicates after this change is not inspected.
- Staging measurement of the actual lock level for the unique build is not provided.

## Verdict reasoning

Duplicates make the statement fail → `BLOCKER` → `BLOCKED`. A report that approves because
`ALGORITHM=INPLACE, LOCK=NONE` is present, or that misses the case-insensitive collision count, has
missed the critical finding.
