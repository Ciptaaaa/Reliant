# Migration Guard Report — MY-003

**Verdict: BLOCK.** The migration cannot succeed as written. Adding `customers_reference_code_unique` on `reference_code` will fail because duplicate values exist under the column's case-insensitive collation (`utf8mb4_0900_ai_ci`). The online-DDL clause does not address this and, for a unique index build, `LOCK=NONE` is not permitted anyway. Do not merge.

---

## 1. Blocking issue: duplicate values under the active collation

**Evidence — `schema.sql`, data facts:**
> `SELECT COUNT(*) FROM ( SELECT reference_code FROM customers GROUP BY reference_code HAVING COUNT(*) > 1 ) d; -> 2,918  -- exact duplicates`
>
> `SELECT COUNT(*) FROM ( SELECT UPPER(reference_code) FROM customers GROUP BY UPPER(reference_code) HAVING COUNT(*) > 1 ) d; -> 3,067  -- case-insensitive duplicates`
>
> `The collation utf8mb4_0900_ai_ci is case-insensitive, so the unique index will fail on 3,067 groups, not 2,918.`

The index is built under the column collation, so the relevant duplicate set is the case-insensitive one: **3,067 groups**, not 2,918. `ALTER TABLE ... ADD UNIQUE KEY` fails on any collision, so this statements errors out. On failure with `ALGORITHM=INPLACE`, MySQL rolls the DDL back; the table stays intact, but the migration is not applied.

## 2. The online-DDL clause is not a safety net

**Evidence — `context.md`:**
> `The agent must find the duplicate evidence and not be reassured by the online-DDL clause.`
>
> `Why this case exists: ... the unique index cannot be built while duplicates exist, and LOCK=NONE may not even be permitted for a unique index build.`

Two separate problems:

- **Correctness:** online DDL prevents blocking, not failure. Duplicates block the build regardless of `LOCK`.
- **Locking level:** `LOCK=NONE` is not supported for unique index creation in MySQL 8.0; the migration uses `LOCK=DEFAULT`, which avoids that specific invalid clause, but does not change the outcome. It still fails.

## 3. Root cause is still live — cleanup alone will not hold

**Evidence — `application-usage.md` (Writes):**
> `app/Console/Commands/ImportCustomers.php:44 — imports from the partner CSV; preserves the case from the CSV, which is sometimes lowercase.`

**Evidence — `application-usage.md` (Not searched):**
> `The partner CSV (external, regenerated weekly).`

**Evidence — `context.md`:**
> `There is a cleanup script ...; the last run was three weeks ago and is known to have left some duplicates.`

The lowercase import path is still active. That creates case-insensitive collisions against existing uppercase codes. A one-time dedupe followed immediately by the index would race with the weekly import and the rolling deploy, so dedupe-then-index is not sufficient by itself.

**Evidence — `application-usage.md` (Cleanup tooling):**
> `DedupeCustomers.php:27 — keeps the row with the lowest id per reference_code and deletes the rest.`

Note also that the dedupe groups on `reference_code` (which is case-insensitive) but the reported duplicate count of 2,918 covers **exact** duplicates. It is unclear whether the script caught all case-variant groups; the "left some duplicates" note suggests it did not.

**Evidence — `application-usage.md` (Writes / lookup):**
> `CustomerController.php:31 — reference looked up with where('reference_code', $input); because the column collation is case-insensitive, abc123 already matches ABC123.`

Existing lookups already behave case-insensitively, so normalizing storage to a single case does not change application semantics.

---

## Required changes before merge

1. **Fix the write path first.** In `ImportCustomers.php`, upper-case `reference_code` on import (consistent with `GenerateReference.php:18`, which writes uppercase). Deploy and verify no lowercase rows are being written.
2. **Dedupe with a case-insensitive, deterministic rule.** Group on `UPPER(reference_code)`; keep the lowest `id` (same rule as existing tooling) and log everything deleted for audit. Verify `SELECT COUNT(*) ... GROUP BY UPPER(reference_code) HAVING COUNT(*) > 1` returns 0 before proceeding.
3. **Split the migration.** Do not combine cleanup and the index in a single DDL statement. Run the data step as a separate, reversible migration or manual step, then apply the index.
4. **Re-run the duplicate check after dedupe and before the index build,** and add a pre-flight guard that aborts the migration if the count is non-zero.
5. **Replace `LOCK=DEFAULT` / `ALGORITHM=INPLACE` with nothing (default), or explicitly `LOCK=NONE` only if MySQL 8.0.36 accepts it for this operation.** Measure on a staging copy. Do not rely on the clause for safety; rely on the data being clean.
6. **Confirm the partner CSV will not reintroduce collisions.** Since the CSV is external and regenerated weekly, either normalize on ingest (step 1) or enforce uniqueness at the partner contract level.

The migration should not be merged in its current form.