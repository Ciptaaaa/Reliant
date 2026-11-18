# Migration Guard Report

## Verdict

**BLOCK — the statement will be rejected by MySQL and the deploy will fail.** It will *not* be applied instantly, and it will *not* silently copy the table. Do not merge as-is.

## Target

- File: `database/migrations/2026_11_16_124000_invoices_currency_varchar.php` (raw SQL form)
- Statement:
  ```sql
  ALTER TABLE `invoices`
    MODIFY COLUMN `currency` VARCHAR(3) NOT NULL,
    ALGORITHM=INSTANT, LOCK=NONE;
  ```
- Table `invoices`, ~18,400,000 rows, peak ~40 inserts/second.

## Finding: `ALGORITHM=INSTANT` is not supported for this change

The core of the developer's reasoning is the claim:

> "It is a metadata change, it should be instant."

`CHAR(3)` → `VARCHAR(3)` is a **column type change**, not a pure metadata operation. `ALGORITHM=INSTANT` is only supported for a specific, evolving set of operations (adding a column at the end, some index changes, some column defaults/renames, etc.), and a data-type change is not in that set. When the requested algorithm is not supported, MySQL **rejects the statement** with an error (e.g. `ER_ALTER_OPERATION_NOT_SUPPORTED` / "ALGORITHM=INSTANT is not supported. Reason: ..."). The `LOCK=NONE` modifier does not rescue it; algorithm support is checked independently.

Consequences of the current migration:

1. **Deploy fails.** The migration is run "as a single step before starting new tasks," so the failure blocks the release.
2. **No damage occurs.** The rejection is safe — there is no partial application and no table copy. This is the one bit of good news. The claim that the table "will be silently copied" is also wrong — the statement **errors out**.
3. **`LOCK=NONE` is moot** for the rejected statement. Once the algorithm is corrected, lock behavior becomes the real concern, see below.

## Finding: the fallback is a table copy with write blocking

To make this change, MySQL must use a copy algorithm (`ALGORITHM=COPY`, or the default `INPLACE` if the type change can be done in place value-wise — for `CHAR`→`VARCHAR` with the same length and charset, the server may choose an in-place rebuild). Either way:

- This is **not** a `LOCK=NONE` operation. A copy requires blocking writes (`LOCK=SHARED`) for the duration, which on 18.4M rows at 40 inserts/second on a hot write path ("every order creates an invoice") means **write outages / insert backlog** during the rebuild.
- No staging test of this specific ALTER has been run, so the actual algorithm the server picks and the lock duration are unknown.

## Additional risk: the "Not searched" consumer

The `application-usage.md` says the partner accounting system that receives invoice PDFs was **"Not searched"**. The change to `VARCHAR(3)` is semantically a no-op for ISO codes (all rows are 3 chars), but we cannot rule out a partner verifying the column type or length behavior (e.g. padding) from PDFs. This is low risk but unverified.

## Recommendations

1. **Do not use `ALGORITHM=INSTANT` for this change.** Remove it, or expect the migration to fail.
2. **Do not use `ALGORITHM=INPLACE` with a blind expectation.** Run the exact ALTER on a staging clone with production-like data volume and measure:
   - which algorithm MySQL picks,
   - lock held (`SHARED` expected),
   - duration at 18.4M rows.
3. **Consider whether the change is needed at all.** `currency` is always 3 chars and is not indexed, not in an FK, and has no generated columns depending on it. If the goal is only "consistency," the type change is not necessary for correctness; capture that decision in the ticket.
4. **If the change proceeds**, plan a maintenance window or a shadow-table online schema change (e.g. `pt-online-schema-change` / `gh-ost`) for the hot write path, since a blocking copy at 18.4M rows will stall invoice creation.
5. **Resolve the "Not searched" gap** with the partner accounting team before merging.
6. **Replace the raw SQL** with a Laravel `Schema` migration (or wrap the `ALTER` so failures are explicit) so the deploy step reports a clear error.

## Evidence relied on

- `migration.sql`: `ALTER TABLE `invoices` MODIFY COLUMN `currency` VARCHAR(3) NOT NULL, ALGORITHM=INSTANT, LOCK=NONE;`
- `schema.sql`: `currency CHAR(3) NOT NULL`; `Rows: ~18,400,000. Inserts: ~40/second at peak.`; hot write path.
- `context.md`: "Deploy runs migrations as a single step before starting new tasks"; "No staging test of this specific ALTER has been run"; developer claim "It is a metadata change, it should be instant."
- `application-usage.md`: `currency` not indexed / no FK / no generated-column dependency; external partner consumer "Not searched."