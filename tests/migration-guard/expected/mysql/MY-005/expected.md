# MY-005 — Expected analysis

## Expected verdict

**CONDITIONAL** — the statement is rejected because `ALGORITHM=INSTANT` does not support a column type
change; the real change needs a rebuild/copy path that must be planned.

## Findings

### F1 — `HIGH` — `lock-and-rewrite` — `ALGORITHM=INSTANT` is not supported for `MODIFY COLUMN`

`ALGORITHM=INSTANT` supports a limited, version-dependent set of operations (add a column at the end and
a few others). Changing a column's data type (`CHAR` → `VARCHAR`) is not one of them. MySQL rejects the
statement with an error such as `ALGORITHM=INSTANT is not supported. Reason: <…>. Try ALGORITHM=INPLACE`;
the deploy fails. The keyword does not make the change instant.

Note the safe failure mode: because `ALGORITHM` was named explicitly, MySQL **refuses** rather than
silently falling back to a blocking copy. A report should say the statement fails, not that it copies
the table behind your back.

**Evidence required:** `migration.sql` (the `ALGORITHM=INSTANT` clause), `schema.sql`.

**Mitigation:** replace with an explicitly supported algorithm that MySQL accepts for this column
change (`ALGORITHM=INPLACE` and the lock level the server reports for this change; if it requires
`COPY`, it will also require `LOCK=SHARED`/exclusive and block writes). Measure on a staging copy of
production size. For an 18M-row hot table, plan the change in a batched expand-and-contract form:
add `currency_v2 VARCHAR(3)`, backfill, dual-write, switch reads, drop the old column later.

### F2 — `HIGH` — `lock-and-rewrite` — the fallback on 18M rows is a rebuild with write impact

If the type change is run with an algorithm that actually supports it, it is an in-place **rebuild of
the table**, not a metadata change: temporary double storage, index rebuilds on `invoices`, metadata
lock, and a long-running DDL that is replayed on the replica. At ~40 inserts/s, `LOCK=SHARED` stalls
writes for the rebuild duration, and the migration reproduces on the replica, causing lag.

**Evidence required:** `migration.sql`, row count and insert rate from `schema.sql`.

**Mitigation:** staging measurement of duration and lock; low-traffic window; replica lag monitoring;
or expand-and-contract to avoid the rebuild entirely.

### F3 — `INFO` — `no-risk-observed` — no data loss from `CHAR` → `VARCHAR` and no dependents

`currency` holds exactly 3 characters in all rows (`schema.sql`), and `CHAR(3)` → `VARCHAR(3)` does not
truncate valid values. The column is not indexed and no generated columns or FKs reference it, so no
dependent objects break. Application reads treat it as a string
(`InvoiceResource.php:22`), so the type change is transparent to code.

## Unknowns that must be reported

- Nobody has run this `ALTER` on staging; the duration and accepted algorithm/lock are unmeasured.
- The replica's tolerance for DDL replay lag is not stated.

## Verdict reasoning

The deploy fails as written (`HIGH`), with a large operational cost for the supported alternative, but
no data is lost → `CONDITIONAL`. A report that says "INSTANT so it is safe" is wrong; a report that says
"the table will be silently rebuilt" is also wrong about the mechanism — the server errors out.
