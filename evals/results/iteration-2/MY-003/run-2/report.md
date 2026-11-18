# Migration Guard Report — MY-003

**Verdict: ❌ BLOCK — do not merge.**

The `ALTER TABLE` will fail in production. There are duplicate `reference_code` values, and the collation is case-insensitive, so the unique index cannot be created. The online-DDL clause does not help here, and `LOCK=NONE` may not even be allowed for this statement.

---

## Findings

### 1. The unique index will fail — duplicates exist (blocking)

Quoted from `schema.sql` data facts:

> `SELECT COUNT(*) FROM ( SELECT reference_code FROM customers GROUP BY reference_code HAVING COUNT(*) > 1 ) d; -> 2,918  -- exact duplicates`
>
> `SELECT COUNT(*) FROM ( SELECT UPPER(reference_code) FROM customers GROUP BY UPPER(reference_code) HAVING COUNT(*) > 1 ) d; -> 3,067  -- case-insensitive duplicates`

And the note right below it:

> `The collation utf8mb4_0900_ai_ci is case-insensitive, so the unique index will fail on 3,067 groups, not 2,918.`

`reference_code` is `utf8mb4_0900_ai_ci`, so `abc123` and `ABC123` count as the same key. Adding `UNIQUE` would reject 3,067 groups of existing rows. MySQL will not build the index: the ALTER fails partway (`ERROR 1062 Duplicate entry ... for key 'customers_reference_code_unique'`).

Note: the developer's own count of 2,918 (case-sensitive) undercounts the problem. The real number of colliding groups is 3,067.

### 2. The cleanup has not fully run, and the data is still changing (blocking)

From `context.md`:

> `There is a cleanup script (artisan customers:dedupe) that runs manually; the last run was three weeks ago and is known to have left some duplicates.`

From `application-usage.md`:

> `DedupeCustomers.php:27 — keeps the row with the lowest id per reference_code and deletes the rest. Requires a manual --force; last run 2026-10-26, before the generator fix.`

So dedupe cannot be assumed to have cleared the table. Also, dedupe only compares exact `reference_code` strings (it groups by the column value, under the case-insensitive collation, so it may actually catch case variants as well; `Unverified`). Either way, the last run predates the described state.

### 3. The import path keeps writing lowercase values (blocking for durability)

From `application-usage.md`:

> `ImportCustomers.php:44 — imports from the partner CSV; preserves the case from the CSV, which is sometimes lowercase.`

And:

> `The partner CSV (external, regenerated weekly).` — under "Not searched".

The generator fix (`GenerateReference.php`) only covers one write path. The CSV importer can still write lowercase codes after dedupe and after the index is added. It will then collide with the uppercase ones and fail at insert time, or keep producing duplicates that the unique index rejects. The weekly regenerated CSV means the exact input values are not known. That is a second, ongoing source of duplicates.

### 4. "Online DDL, no downtime" — not a safe assumption here

From `context.md`:

> `Using online DDL so there is no downtime.`

The migration uses `ALGORITHM=INPLACE, LOCK=DEFAULT`, but `context.md` itself says:

> `LOCK=NONE may not even be permitted for a unique index build`

Even if an in-place (online) unique-index build were permitted, it would still abort on duplicates. The locking strategy is not the risk here; the data is. Whether MySQL 8.0.36 can do this as an online (non-blocking) build for a *unique* secondary index is `Unverified` for this exact statement. Do not base the plan on it.

---

## What is actually required before this can merge

1. **Fix the data first, then re-verify counts = 0.** The dedupe tool must be run with `--force` *after* the generator fix and after the CSV importer is fixed, or once the importer is constrained. Before adding the index, re-run the case-insensitive duplicate check and require 0 groups.
2. **Normalize case.** Either uppercase values at write time on all paths (the CSV importer is the known gap), or add a `CHECK`/generated column / trigger. Uppercasing makes the data invariant and matches the intended "uppercase alphanumeric" format.
3. **Do not use a unique index over the collation until data is clean.** A `UNIQUE` built while duplicates exist fails, and after it succeeds the importer can still break inserts.
4. **Coordinate with the deploy.** This is a rolling deploy (`context.md`) against a large table (620k rows). Sequence: data cleanup → importer/uppercase enforcement → verify duplicates = 0 → create index (separately, online if supported) → deploy code. Keep the index creation out of the same migration window as the code that assumes uniqueness.

## Required evidence still missing (`Unverified`)
- Post-`--force` dedupe result. The last known run (2026-10-26) is stale, and the current duplicate counts are 3,067 case-insensitive groups.
- Whether dedupe already treats `abc123` and `ABC123` as one key (it groups by `reference_code`, which is collation-dependent), or only exact duplicates.
- Contents of the partner CSV (external, weekly). It is the case-preserving writer.
- Whether MySQL 8.0.36 permits `LOCK=NONE` / non-blocking build for a `UNIQUE` secondary index on this engine.

## Bottom line
The `ALTER` as written **fails** because 3,067 case-insensitive duplicate groups exist. It also does not solve the ongoing cause: the CSV importer writes lowercase codes that collide with uppercase ones. **Block until dedupe is re-run to zero duplicates, all write paths normalize case, and the counts are re-verified.** Re-run it as a separate, verified step after that.