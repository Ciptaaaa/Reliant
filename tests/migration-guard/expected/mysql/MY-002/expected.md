# MY-002 — Expected analysis

## Expected verdict

**CONDITIONAL** — the statement fails because 84,302 `NULL` rows exist; the fix requires a backfill
and an explicit online-DDL algorithm.

## Findings

### F1 — `HIGH` — `not-null-add` — `MODIFY … NOT NULL` fails on existing `NULL`s

`schema.sql` records 84,302 rows with `method IS NULL` out of 11.24M. MySQL validates existing rows
when the column is made `NOT NULL`; the `ALTER` fails with
`Invalid use of NULL value` (`ERROR 1138`). The deploy fails.

Unlike PostgreSQL, MySQL has **no `NOT VALID`** for `NOT NULL`, so there is no way to add the
constraint first and validate later: the data must be clean **before** running the `ALTER`.

**Evidence required:** `schema.sql` (84,302), `migration.sql`.

**Mitigation:** backfill in batches **before** the DDL, with a resumable predicate:

```sql
-- repeat until 0 rows; batch 1,000–5,000; pause between batches
UPDATE payments SET method = 'unknown'
 WHERE id IN (SELECT id FROM (SELECT id FROM payments WHERE method IS NULL LIMIT 2000) t);
```

Then change the application so `ImportLegacyPayments.php:71` never writes `NULL`, and only then run the
`ALTER`.

### F2 — `HIGH` — `lock-and-rewrite` — `MODIFY COLUMN` without explicit algorithm is a rebuild-ish path

The migration has no `ALGORITHM=`/`LOCK=`. Changing a column definition to add `NOT NULL` is not one of
the operations MySQL can do with `ALGORITHM=INSTANT` on a nullable column; MySQL either rebuilds the
table (`INPLACE`, possibly with a lock) or copies it (`COPY`, blocking writes) depending on what the
server chooses, and the choice depends on version and table definition. Because the statement does not
state `ALGORITHM`, you cannot promise a non-blocking path. Declare the intent explicitly and let the
server reject it if it cannot comply:

```sql
ALTER TABLE payments MODIFY COLUMN method VARCHAR(24) NOT NULL, ALGORITHM=INPLACE, LOCK=SHARED;
```

Measure on a staging copy of production size. If the server cannot do it in place, it returns an error
instead of silently copying — that is the point of naming the algorithm.

**Evidence required:** `migration.sql`, row count in `schema.sql`.

**Mitigation:** run the explicit `ALGORITHM=INPLACE, LOCK=SHARED`; schedule a low-traffic window;
expect replication lag on the reporting replica while DDL is applied and replayed.

### F3 — `MEDIUM` — `application-compatibility` — legacy importer still writes `NULL`

`app/Console/Commands/ImportLegacyPayments.php:71` bulk-inserts legacy rows without `method`. After the
`NOT NULL` is in place, the next run of the importer fails with `Column 'method' cannot be null`
(strict mode). Fix the importer (default `'unknown'`) or the migration rolls the deploy forward into
a broken cron job.

**Evidence required:** `application-usage.md` (ImportLegacyPayments).

### F4 — `INFO` — `no-risk-observed` — reporting tolerates `NULL` and the backfill value

`MethodBreakdown.php:19` already uses `COALESCE(method,'unknown')`, so `'unknown'` is an acceptable
backfill value and reporting does not need to change.

## Unknowns that must be reported

- Whether `ImportLegacyPayments` runs during the deploy window (out-of-band scheduler) is unknown.
- The measured lock duration on a staging copy is not provided.
- Whether the replica will lag far enough during the DDL to break reporting SLAs is not measured.

## Verdict reasoning

No data destroyed; the statement fails on existing data (`HIGH`) and has a rebuild/lock cost plus a
writer that keeps producing `NULL`s (`MEDIUM`) → `CONDITIONAL`. Saying `APPROVED` because "the
checkout flow always sets method" ignores the 84,302 historical rows.
