# Migration Guard Report — MY-005 (`invoices.currency`)

## Verdict: **BLOCK — deploy will fail**

The migration as written will be **rejected by MySQL** before it does anything. `ALGORITHM=INSTANT` does not support changing a column's data type, so the server raises an error (it does **not** fall back to a table copy/rebuild automatically). The deploy step fails; no partial migration is applied.

## Evidence

### 1. The operation is a column type change
```sql
ALTER TABLE `invoices`
  MODIFY COLUMN `currency` VARCHAR(3) NOT NULL,
  ALGORITHM=INSTANT, LOCK=NONE;
```
Changing `CHAR(3)` → `VARCHAR(3)` is a change of the column's data type/storage representation. It is not a purely metadata-only edit (e.g. renaming, changing `DEFAULT`, changing `NOT NULL` on a nullable column, etc., which are the operations INSTANT covers on 8.0.36).

### 2. INSTANT is not supported for type changes → statement is rejected
MySQL 8.0.36's online DDL support table does **not** list `MODIFY COLUMN` with a type change under `ALGORITHM=INSTANT` (or `ALGORITHM=INPLACE` in-place without rebuild). When an explicitly requested algorithm is unsupported, the server returns an error such as:

> `ERROR 1846 (0A000): ALGORITHM=INSTANT is not supported. Reason: Cannot change column type. Try ALGORITHM=COPY.`

The statement therefore **errors out**. This is the safe failure mode described in the case: **the deploy fails, but the table is not copied and nothing is changed.**

### 3. The "it's metadata, it'll be instant" claim is false
The presence of the `ALGORITHM=INSTANT` keyword in the SQL does **not** make the operation instant. The keyword is a *requirement*, not a *request-with-fallback*: if the server cannot satisfy it, it refuses.

### 4. The opposite error does not apply either
Because the statement is rejected, MySQL will **not** silently fall back to a `COPY` rebuild. So it is **not** accurate to say "the table will be copied with a lock" — that would only be true if the algorithm clause were removed or changed to `COPY`.

## Fallback (if the change is still desired)

`CHAR(3)` and `VARCHAR(3)` for a fixed 3-char ISO code are effectively equivalent in practice; the "consistency" motivation has no functional benefit here. If the change is genuinely wanted, drop the algorithm/lock clause (or use `ALGORITHM=COPY, LOCK=NONE` only after verification) and treat it as a potential **table rebuild** on ~18.4M rows. That rebuild:

- runs against a table taking **~40 inserts/second**,
- will produce a large DDL that competes for I/O and metadata locks,
- must be tested on staging first (none has been run per deployment facts),
- should be scheduled/controlled (e.g. dedicated maintenance window or a gh-ost/pt-osc style tool), not run inline in the pre-start migration step of a rolling deploy.

Given `application-usage.md` shows `currency` is unindexed, not in any FK, and not referenced by generated columns, and the values are always 3-character ISO codes, there's no technical need to alter the type. The safest action is to **not merge this migration**; if the team wants `VARCHAR`, redo it as a separately planned, staged rebuild after a staging test.

## Required actions before merge
1. Do **not** merge `migration.sql` as-is — it will fail the deploy.
2. Either drop the migration entirely (recommended, given equivalence of `CHAR(3)`/`VARCHAR(3)` for this use), or rewrite it without `ALGORITHM=INSTANT` and plan for a table rebuild.
3. Run the actual ALTER on staging first and record the algorithm/lock outcome and timing.