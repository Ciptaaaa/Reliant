# Migration Guard Report — MY-003

## Verdict

**🔴 BLOCK — do not merge.**

The migration adds a `UNIQUE` index to a column that is known to contain categories of duplicates that the `UNIQUE` index will reject. It will fail at execution time (or, worse, be attempted repeatedly against a live table). The "online DDL" (`ALGORITHM=INPLACE`, `LOCK=DEFAULT`) clause does not change this outcome; it addresses lock duration, not data validity, and in any case is not a guarantee the engine will honor for a unique index build.

## Migration under review

```sql
ALTER TABLE `customers`
  ADD UNIQUE KEY `customers_reference_code_unique` (`reference_code`),
  ALGORITHM=INPLACE, LOCK=DEFAULT;
```

## Blocking evidence

### 1. The column has 3,067 case-insensitive duplicate groups — more than the unique index can tolerate

From `schema.sql` (data facts, 2026-11-16):

> `SELECT COUNT(*) FROM ( SELECT reference_code FROM customers GROUP BY reference_code HAVING COUNT(*) > 1 ) d; -> 2,918 -- exact duplicates`
>
> `SELECT COUNT(*) FROM ( SELECT UPPER(reference_code) FROM customers GROUP BY UPPER(reference_code) HAVING COUNT(*) > 1 ) d; -> 3,067 -- case-insensitive duplicates`
>
> "The collation utf8mb4_0900_ai_ci is case-insensitive, so the unique index will fail on 3,067 groups, not 2,918."

The table collation is `utf8mb4_0900_ai_ci` (case-insensitive). A `UNIQUE KEY` on `reference_code` therefore treats `abc123` and `ABC123` as equal. There are **3,067 duplicate groups**, every one of which would cause `ADD UNIQUE KEY` to fail with `ER_DUP_ENTRY`. The 2,918 figure is a red herring — it understates the problem.

### 2. The duplicate source is still active in one write path

From `application-usage.md`:

> `app/Console/Commands/ImportCustomers.php:44` — imports from the partner CSV; **preserves the case from the CSV**, which is sometimes lowercase.

The generator bug was fixed, but the CSV import path still writes lowercase codes, which (given the case-insensitive collation) collide with existing uppercase codes. The vulnerable writer remains.

### 3. The cleanup tool does not make the table safe

From `application-usage.md`:

> `DedupeCustomers.php:27` — keeps the row with the lowest `id` per `reference_code` and deletes the rest. Requires a manual `--force`; last run 2026-10-26, before the generator fix.

And from `context.md`:

> "There is a cleanup script (`artisan customers:dedupe`) that runs manually; the last run was three weeks ago and is known to have left some duplicates."

The dedupe tool operates on exact `reference_code` matches; the live data still shows 2,918 exact and 3,067 case-insensitive duplicate groups. The cleanup has not cleared the table, and it is not run by the migration.

### 4. The online-DDL clause does not rescue this

`context.md` explicitly notes the trap:

> "The developer has done the right thing for locking (`INPLACE`, `LOCK=NONE`) and is still wrong… the unique index cannot be built while duplicates exist, and `LOCK=NONE` may not even be permitted for a unique index build."

Two independent problems:
- **Data correctness** — duplicates make the unique index unbuildable regardless of algorithm.
- **DDL capability** — MySQL's support matrix for online DDL on adding a unique secondary index permits `INPLACE` but restricts concurrent DML; `LOCK=DEFAULT`/`LOCK=NONE` is not guaranteed to be honored, and the build will still take a metadata lock on the table for at least part of the operation on 620k rows. The claim of "no downtime" is not established by this clause alone.

## Additional risks (non-blocking but must be addressed)

- **Rolling deploy and collation mismatch.** `where('reference_code', $input)` in `CustomerController.php:31` relies on case-insensitive matching. Any future normalization (e.g., `utf8mb4_bin` + lowercasing) must be coordinated with the application deploy or the lookup behavior changes.
- **Partner CSV is an uncontrolled input.** It is regenerated weekly and "not searched." A lowercase-heavy week can reintroduce collisions immediately after a cleanup.

## Required actions before this can be approved

1. **Normalize before enforcing.** Either `UPDATE customers SET reference_code = UPPER(reference_code)` (checking for resulting collisions first) or standardize the generator and importer to emit uppercase.
2. **Re-run dedupe on the case-insensitive key.** The existing tool keys on exact strings; it must be adapted (or a one-off script run) to collapse by `UPPER(reference_code)`, preserving the lowest `id` and rewiring any foreign references.
3. **Fix the CSV importer** (`ImportCustomers.php:44`) to uppercase on ingest, so the constraint is not immediately violated by the next import.
4. **Re-verify counts** — confirm the same pair of duplicate-count queries both return `0` on the production snapshot immediately before the migration.
5. **Reconsider the DDL clause.** After data is clean, if the migration is still desired, validate that MySQL 8.0.36 actually accepts the requested algorithm/lock mode for a unique index on this table (test on a replica). Do not claim "no downtime" without evidence.
6. **Decide on the index semantics.** If case-insensitive uniqueness is intended, a unique index on `UPPER(reference_code)` (via a generated column) is more explicit; if case-sensitive uniqueness is intended, the column collation or the comparison must be changed first.

## Summary

| Check | Result |
|---|---|
| Duplicates present? | Yes — 2,918 exact, **3,067 case-insensitive** |
| Duplicate writer still active? | Yes — CSV importer preserves case |
| Cleanup complete? | No — last run 2026-10-26, duplicates remain |
| DDL clause sufficient? | No — algorithm/lock does not fix data validity |
| **Overall** | **BLOCK** |

The migration as written cannot succeed on the current data and must not be merged. Fix the data (normalize + dedupe case-insensitively), fix the importer, re-verify counts are zero, re-test the DDL on a replica, and then resubmit.