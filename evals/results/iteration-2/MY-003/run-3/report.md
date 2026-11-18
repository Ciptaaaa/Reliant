# Migration Guard Report — MY-003

## Verdict

**BLOCK — do not merge as written.**

The migration will fail: the unique index cannot be created while duplicates exist, and the online-DDL clause does not save it. Two independent problems exist (duplicate data, and the `LOCK=DEFAULT` / unique-index interaction), plus a latent correctness issue that survives the migration (case handling).

## Findings

### 1. Hard failure: duplicate values in the indexed column (blocking)

The migration adds `UNIQUE KEY customers_reference_code_unique (reference_code)` over 618,904 rows. The recorded data facts show duplicates:

- Exact duplicates: `3,067` case-insensitive groups vs `2,918` exact — per `schema.sql`:
  > `SELECT COUNT(*) … GROUP BY reference_code HAVING COUNT(*) > 1` → **2,918** — exact duplicates
  > `SELECT COUNT(*) … GROUP BY UPPER(reference_code) HAVING COUNT(*) > 1` → **3,067** — case-insensitive duplicates
  > "The collation utf8mb4_0900_ai_ci is case-insensitive, so the unique index will fail on **3,067** groups, not 2,918."

Because the column collation is `utf8mb4_0900_ai_ci`, `abc123` and `ABC123` are treated as equal for uniqueness. So even before reaching the 2,918 exact-duplicate groups, the lowercased import rows will collide with existing uppercase ones. The `ALTER` will abort with **ER_DUP_ENTRY (1062)**. The migration does not deduplicate, transform, or pre-check anything.

Evidence for *why* the duplicates exist:

- `application-usage.md`: `ImportCustomers.php:44` — "imports from the partner CSV; **preserves the case from the CSV**, which is sometimes lowercase."
- `DedupeCustomers.php:27` cleanup is manual (`--force`) and "last run 2026-10-26, before the generator fix" — so recent duplicates are **not cleaned up**.

### 2. `LOCK=DEFAULT` is not what the developer thinks (blocking for the "no downtime" claim)

- The migration uses `ALGORITHM=INPLACE, LOCK=DEFAULT`, **not** `LOCK=NONE`. `LOCK=DEFAULT` permits whatever locking MySQL deems necessary.
- Even if it were `LOCK=NONE`, unique-index creation on InnoDB generally requires an exclusive/stronger lock at some phase or is not permitted, so the "no downtime" reassurance is false. `context.md` itself flags this:
  > "the unique index cannot be built while duplicates exist, and `LOCK=NONE` may not even be permitted for a unique index build."
- The developer is "right about locking … and still wrong" (`context.md`) — the online-DDL clause does not make a failing build succeed.

### 3. Data correctness gap: lowercase writes are not prevented going forward

Even after a successful dedupe + index build, uniqueness is enforced case-insensitively (fine), but:

- `GenerateReference.php:18` writes uppercase — consistent.
- `ImportCustomers.php:44` writes whatever case the CSV has — this is the source of ambiguity. The index will *reject* a lowercase collision, turning a silent duplicate into a hard import error. That may be desirable, but it means the `ImportCustomers` path must be changed to normalize (uppercase) before insert, or imports will fail at runtime.
- The partner CSV is external and "**regenerated weekly**" (`application-usage.md`, "Not searched"), so a new collision can appear after the migration. Normalization in the importer is required, not optional.

### 4. Lookup semantics (informational)

`CustomerController.php:31` looks up with `where('reference_code', $input)` and relies on case-insensitive collation. That behavior is unchanged by this migration. No action required, but note that if anyone ever changes the collation to `_bin`, this lookup breaks.

## Required changes before merge

**Do not run the `ALTER` directly.** Sequence:

1. **Write a code migration / artisan task** that:
   - Normalizes `reference_code` to uppercase (`UPDATE customers SET reference_code = UPPER(reference_code)`), or at least to a single canonical form, in batches.
   - Re-runs dedupe against `UPPER(reference_code)` and verify the exact-duplicate and case-insensitive-duplicate counts both reach **0**:
     ```sql
     SELECT COUNT(*) FROM (SELECT UPPER(reference_code) FROM customers
       GROUP BY UPPER(reference_code) HAVING COUNT(*) > 1) d;  -- must be 0
     ```
   - Only then add the unique index.

2. **Fix `ImportCustomers.php:44`** to uppercase (or canonicalize) before insert, and ideally upsert rather than blind insert. This is the actual ongoing source of collisions.

3. **Fix the migration itself**:
   - Use `ALGORITHM=INPLACE, LOCK=NONE` *only* if the pre-check and normalization guarantee no duplicates; otherwise expect failure.
   - Add a **defensive pre-check** in the migration that aborts with a clear message if the duplicate count is non-zero, so a stale environment fails loudly rather than midway through DDL.
   - Consider a two-step deployment: (a) normalize data, (b) add the index, in separate migrations that run sequentially, to keep each step reversible and verifiable.

4. **Verification query** post-migration:
   ```sql
   SELECT COUNT(*) FROM (
     SELECT UPPER(reference_code) FROM customers
     GROUP BY UPPER(reference_code) HAVING COUNT(*) > 1
   ) d;  -- must be 0
   ```

## Summary table

| Check | Result | Evidence |
|---|---|---|
| Unique index buildable with current data | ❌ | 3,067 case-insensitive duplicate groups (`schema.sql`) |
| Migration handles duplicates | ❌ | No dedupe/normalization in `migration.sql` |
| Lock clause suitable for zero downtime | ❌ (at best, misleading) | `LOCK=DEFAULT`, not `LOCK=NONE`; unique build may fall back to stronger locks (`context.md`) |
| Ongoing writes prevent duplicates | ❌ | `ImportCustomers.php:44` preserves CSV case; CSV regenerated weekly |
| Cleanup guaranteed complete | ❌ | `DedupeCustomers.php` manual, last run before generator fix |
| Lookup semantics preserved | ✅ (informational) | Case-insensitive collation unchanged |

**Recommendation: block merge.** Require the normalization + dedupe step and importer fix first, and rewrite the migration to include a duplicate pre-check and the correct locking options.