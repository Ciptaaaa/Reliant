# Migration Guard Report — MY-003

## Verdict

**BLOCK. Do not merge.**

The migration will fail on this dataset. It also has a correctness problem that a successful run would not fix, and the `LOCK=DEFAULT` clause plus the collation make the "no downtime" reasoning unsound. The online-DDL clause does not change any of this.

## Evidence

**1. The index cannot be built — duplicates exist right now.**

Schema facts, quoted from `schema.sql`:

```
--   SELECT COUNT(*) FROM customers;                                                  -> 618,904
--   SELECT COUNT(*) FROM (
--     SELECT reference_code FROM customers GROUP BY reference_code HAVING COUNT(*) > 1
--   ) d;                                                                             -> 2,918  -- exact duplicates
--   SELECT COUNT(*) FROM (
--     SELECT UPPER(reference_code) FROM customers
--     GROUP BY UPPER(reference_code) HAVING COUNT(*) > 1
--   ) d;                                                                             -> 3,067  -- case-insensitive duplicates
-- The collation utf8mb4_0900_ai_ci is case-insensitive, so the unique index will fail on 3,067
-- groups, not 2,918.
```

`ADD UNIQUE KEY ... (reference_code)` will fail with `ERROR 1062 (23000): Duplicate entry` on `customers`. The column collation is `utf8mb4_0900_ai_ci`, so the unique constraint applies case-insensitively. 3,067 groups violate it, not 2,918.

**2. The cleanup already ran and left duplicates behind.**

From `application-usage.md`:

> `app/Console/Commands/DedupeCustomers.php:27` — keeps the row with the lowest `id` per `reference_code` and deletes the rest. Requires a manual `--force`; last run 2026-10-26, before the generator fix.

From `context.md`:

> There is a cleanup script (`artisan customers:dedupe`) that runs manually; the last run was three weeks ago and is known to have left some duplicates.

The data facts are from 2026-11-16, after the last dedupe run. The duplicates above are real and still there.

**3. The dedupe tool cannot clear the index for you.** `DedupeCustomers.php` groups by `reference_code` under the same case-insensitive collation, so it would collapse `ABC123` and `abc123` into one row: it "keeps the row with the lowest `id` per `reference_code`." That resolves the constraint but picks whichever row happens to have the lower `id`, not the row that matches the canonical uppercase generator. That is silent data loss of the wrong row.

**4. Lowercase writes will keep coming.**

From `application-usage.md`:

> `app/Console/Commands/ImportCustomers.php:44` — imports from the partner CSV; **preserves the case from the CSV**, which is sometimes lowercase.

The generator fix does not cover this path. The CSV is external and "regenerated weekly," and it was **not searched** ("Not searched — The partner CSV"). A unique index added now, after a one-off dedupe, will fail again on the first weekly import that has a lowercase row matching an existing uppercase code.

**5. The online-DDL clause is not the protection it looks like.**

- `LOCK=DEFAULT` is not `LOCK=NONE`. Under `DEFAULT`, MySQL picks the least restrictive lock it can, which may still include something worse than `NONE`. `context.md` correctly notes that "the agent must … not be reassured by the online-DDL clause."
- Adding a **unique** index is not always permitted with `LOCK=NONE`. In MySQL 8.0, if the operation ends up needing a table copy or an exclusive lock, the statement is rejected. With duplicates present it will fail first anyway.
- Even in the best case (INPLACE, `LOCK=NONE`), the build scans and locks nothing but still costs I/O. On a rolling deploy with two app versions live, step 6 below becomes relevant.

**6. Rolling deploy ordering problem.** A new unique constraint combined with an application that still writes lowercase codes (`ImportCustomers.php:44`) and looks up case-insensitively (`CustomerController.php:31`: "because the column collation is case-insensitive, `abc123` already matches `ABC123`") means the constraint is not compatible with the currently deployed code. That needs to be fixed before or together with the schema change.

## What must happen before this is merged

1. **Measure again and narrow scope.** Re-run the 3,067 count on the current data right before the migration, not on the 2026-11-16 snapshot. Re-check the exact duplicates count after any import.
2. **Clean up case-insensitively, deterministically, and in a scripted migration step.** Do not rely on `customers:dedupe` with its current `GROUP BY reference_code` logic. Canonicalize to uppercase first (for example, update the `abc123` rows to the uppercase value or merge them with an explicit rule), then verify 0 remaining `UPPER(reference_code)` duplicates. Do this in a batched, idempotent step.
3. **Close the lowercase write path.** Change `ImportCustomers.php:44` to uppercase or to reject non-uppercase codes before the constraint is added. Otherwise the next weekly CSV import breaks the index.
4. **Check the partner CSV for uppercase collisions.** It was explicitly not searched. Two different customers with `abc123` and `ABC123` are two different identities today; merging them is a business decision, not a cleanup detail. That decision needs an owner, not a dedupe script.
5. **Use the right locking.** Once there are 0 violating rows, the repeatable form is `ALGORITHM=INPLACE, LOCK=NONE` on the unique index add (InnoDB permits this for a secondary index). Keep `LOCK=DEFAULT` only if you have verified it degrades to `NONE`. Expect and handle `ERROR 1846` if the engine refuses the requested algorithm.
6. **Sequence with the rolling deploy.** Apply the constraint only after all app instances have been on the uppercase-writing, null-safe code. Or use a reversible two-step: backfill and normalize, deploy, then add the constraint.

## Summary

| Check | Result |
|---|---|
| Will the migration succeed? | No — 3,067 case-insensitive duplicate groups. |
| Does online DDL fix it? | No. `LOCK=DEFAULT` is not `LOCK=NONE`, and a unique index may not permit `NONE`. |
| Is the data clean for this constraint? | No — dedupe ran before the fix and left duplicates. |
| Will it stay clean after the migration? | No — `ImportCustomers.php:44` writes lowercase from the CSV. |
| Safe to merge as-is? | **No.** |

The developer used the right instinct (constraint) and the wrong evidence (online DDL). The blocker is the data, and the data is neither clean nor stable enough for a unique index yet.