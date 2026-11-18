# MY-006 — Expected analysis

## Expected verdict

**CONDITIONAL** — the change is safe in data terms but the chosen `LOCK=SHARED` combined with a
2–6-minute shared-lock transaction on the primary creates a metadata-lock pile-up.

## Findings

### F1 — `HIGH` — `lock-and-rewrite` — metadata-lock pile-up behind the settlement report

`ALTER TABLE … LOCK=SHARED` needs a metadata lock that conflicts with the one held by
`SettlementReport.php:58`, a transaction that touches `shipments` for 2–6 minutes every 15 minutes on the
**primary**. The `ALTER` waits for it. While it waits, new queries on `shipments` request the same MDL
and the server serialises those requests, so the high-QPS tracking endpoint can queue behind the
pending `ALTER` for as long as it waits. The result is a multi-minute stall of the busiest table, not
just a stall of the `ALTER`.

**Evidence required:** `migration.sql` (LOCK=SHARED), `application-usage.md` (SettlementReport runs on
the primary for 2–6 minutes).

**Mitigation:**

1. Do not run this while the settlement window is active. Schedule the migration immediately **after**
   the settlement job finishes, or move the settlement report to the replica first.
2. Prefer `ALGORITHM=INSTANT, LOCK=DEFAULT` for the add (see F2). If INSTANT applies, the MDL is held
   for a fraction of a second and the pile-up risk collapses.
3. Set `lock_wait_timeout` low (e.g. `SET SESSION lock_wait_timeout = 5;`) and retry, so a busy moment
   fails fast instead of stalling the table.
4. Watch `performance_schema.metadata_locks` (LOCK_STATUS = PENDING) and `sys.schema_table_lock_waits`.

**Approval: schedule (F1)** — reversible, but needs a window, throttle, and abort conditions.

### F2 — `HIGH` — `lock-and-rewrite` — explicit `INPLACE`/`LOCK=SHARED` forces a rebuild

Adding a column at the end with a constant default is an operation MySQL 8.0.12+ can perform with
`ALGORITHM=INSTANT`. By forcing `INPLACE` + `LOCK=SHARED` the developer converts a metadata-only change
into a table rebuild that blocks writes for its duration. The statement is valid and MySQL will do the
expensive thing, because the requested algorithm is legal for this operation — it is just not the
cheapest one.

**Evidence required:** `migration.sql`, `schema.sql` (MySQL 8.0.36).

**Mitigation:** request `ALGORITHM=INSTANT, LOCK=DEFAULT` and let MySQL reject it if it cannot comply.
Note that INSTANT supports only `LOCK=DEFAULT`; a combined `INSTANT, LOCK=NONE` is invalid and will
fail. Verify on staging with `SHOW WARNINGS`.

## Unknowns that must be reported

- Whether the settlement report has been moved to the replica; current code runs it on the primary.
- The duration and actual lock taken on staging are unmeasured.

## Verdict reasoning

The migration as written rebuilds a busy table and its MDL can stall the tracking endpoint (`HIGH`),
though no data is at risk → `CONDITIONAL`. A `LOW` verdict because "it only adds a column" misses both
the forced rebuild and the settlement transaction.

## Correction note

This fixture states the version boundary as "8.0.12+ supports INSTANT add-column at the end of the
table", verified against the 8.0.12 release notes. 8.0.29 extended INSTANT support (add at any
position, instant `DROP COLUMN`); the fixture does not depend on that boundary.

### F3 — `INFO` — `no-risk-observed` — data and compatibility

`status` is added with `NOT NULL DEFAULT 'pending'`, so rows from deployed code that insert without
`status` continue to work, and existing rows are readable. No data is changed or removed.

## Unknowns that must be reported

- Exactly which version introduced INSTANT add-column is stated as 8.0.12 (verify against the target
  server's release notes); if the target were 5.7, the operation would be a rebuild regardless.
- The duration and actual lock taken on staging are unmeasured.

## Verdict reasoning

The migration as written can stall the busiest table via MDL queueing (`HIGH`) though no data is at
risk → `CONDITIONAL`. A `LOW` verdict because "it only adds a column" misses both the lock clause and
the settlement transaction.
