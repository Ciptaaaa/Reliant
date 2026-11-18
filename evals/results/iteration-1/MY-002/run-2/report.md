# Migration Guard Report — MY-002

## Verdict: **BLOCK — do not merge as written**

The migration will fail on deploy against production data, and even if the `NULL` rows were cleaned first, it is written such that MySQL may choose a blocking copy/rebuild path with no guard against it.

---

## Findings

### 1. Blocking: The migration will fail — 84,302 `NULL` rows exist

`schema.sql` data facts:

> ```
> SELECT COUNT(*) FROM payments;                         -> 11,240,911
> SELECT COUNT(*) FROM payments WHERE method IS NULL;    -> 84,302
> ```

The migration:

```sql
ALTER TABLE `payments` MODIFY COLUMN `method` VARCHAR(24) NOT NULL;
```

MySQL validates existing rows when adding `NOT NULL`. With 84,302 `NULL` rows present, this statement aborts with an error (e.g. `Invalid use of NULL value` / `Column 'method' cannot be null`). The deploy fails at the migration step. Because MySQL DDL implicitly commits (`context.md`: "MySQL DDL implicitly commits"), there is no transaction to roll back — the deploy step simply errors.

The developer's premise is contradicted by the evidence. The rationale ("always set by the checkout flow") only covers `CapturePayment.php:52`. It does not cover `ImportLegacyPayments.php:71`:

> "bulk insert from the old gateway export; the export omits `method` for 2 columns of legacy data (so `NULL` can still be written by this path)."

So there is an **active write path that still produces `NULL`**, not just historical rows. Backfilling without addressing that path will let new `NULL`s reappear.

### 2. Blocking: No explicit `ALGORITHM`/`LOCK` — rebuild/lock path is unguarded

`context.md` deployment facts:

> "No `ALGORITHM=` / `LOCK=` clause in the migration."

> "`MODIFY COLUMN` without an explicit `ALGORITHM` may fall back to a copy/rebuild path."

Omitting `ALGORITHM`/`LOCK` lets the server pick whatever it can: on 8.0.36 a `VARCHAR→VARCHAR NOT NULL` change *usually* qualifies for `INPLACE` (metadata-only for widening/`NULL` tightening when no rewrite is needed), but we should not bet a 11M-row table on "usually." Without the clause:

- If MySQL falls back to `COPY`, it rebuilds the whole table (11.2M rows), holds a shared lock blocking writes for the duration, and streams a large amount of DDL/binlog to the replica → **replication lag**.
- Reporting runs on the replica (`context.md`: "Replica reads: reporting queries run on the replica and lag during expensive DDL"; `MethodBreakdown.php:19` queries the replica). Lag directly degrades reporting freshness.

Even the *good* path (INSTANT/INPLACE metadata change) still requires an exclusive metadata lock (MDL) briefly — which today would be blocked by any long-running query on `payments`, plus the validation scan itself must read all rows to find the `NULL`s.

### 3. Note (not blocking): Reads are `NULL`-tolerant, views are not

- `MethodBreakdown.php:19` uses `COALESCE(method,'unknown')` → tolerant, fine.
- `resources/views/admin/payments/show.blade.php:33` renders `{{ $payment->method }}` → currently empty for `NULL`; after backfill it will show whatever sentinel we choose. This is a **product decision**, not a migration blocker, but the backfill value must be agreed (e.g. `'unknown'` vs `'legacy'`).

### 4. Not searched / residual risk

`application-usage.md` marks "The legacy gateway export file contents (external)" as **not searched**. We cannot confirm the export never emits a non-NULL-but-invalid value, nor whether those "2 columns" are constant. This is an accepted open risk; the backfill sentinel should be chosen to be unambiguous regardless.

---

## Required changes before merge

1. **Backfill the 84,302 `NULL` rows first**, in batches (e.g. `WHERE method IS NULL LIMIT 5000` loops) to avoid a long-row-lock transaction. Choose a sentinel consistent with reporting, e.g. `'unknown'` / `'legacy'`.

2. **Fix the write path.** `ImportLegacyPayments.php:71` must stop writing `NULL` (default the missing columns to the sentinel, or reject the import). Otherwise new `NULL`s will re-break the constraint after it is added. *(The developer's "always set" claim is false for this path per `application-usage.md`.)*

3. **Make the DDL explicit and bounded.** Add clauses, and run in a maintenance window or during low write traffic:

```sql
ALTER TABLE `payments`
  MODIFY COLUMN `method` VARCHAR(24) NOT NULL,
  ALGORITHM=INPLACE, LOCK=SHARED;
```

- `ALGORITHM=INPLACE` tells MySQL to use the in-place path or **fail fast** rather than silently rebuilding; if it cannot do INPLACE for `NOT NULL` tightening on 8.0.36, the statement errors immediately instead of doing a hidden copy.
- `LOCK=SHARED` permits reads but blocks writes for the (short) validation/metadata phase; if a longer lock is needed, it errors rather than blocking writes for minutes.
- Verify on a staging clone of production volume before running: confirm the chosen algorithm and expected duration.

4. **Verify replica lag budget.** The DDL is small in the INPLACE case, but the *validation* still scans all rows. Run when replica lag is low and monitor during the deploy; reporting on the replica (`MethodBreakdown.php`) will lag if the binlog event is large.

5. **(Optional, safer) Two-phase order:** backfill → deploy code that writes only non-NULL → run the explicit ALTER → merge. This avoids a window where old and new code coexist against a column that rejects `NULL`.

---

## Summary

| Check | Result |
|---|---|
| `NULL` rows present | **84,302** → ALTER will fail |
| Active path still writing `NULL` | **Yes** (`ImportLegacyPayments.php:71`) |
| DDL algorithm/lock explicit | **No** → possible COPY/rebuild + replica lag |
| Reads tolerate `NULL` | Yes (report); view renders empty |
| Verdict | **BLOCK** until backfill + write-path fix + explicit `ALGORITHM`/`LOCK` |