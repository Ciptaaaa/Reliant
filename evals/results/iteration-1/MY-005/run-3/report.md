# Migration Guard Report — MY-005

## Verdict

**BLOCK — the migration will fail at execution time.** The statement requests `ALGORITHM=INSTANT` for a column type change (`CHAR(3)` → `VARCHAR(3)`), which is not an INSTANT-capable operation in MySQL 8.0.36. MySQL will **reject the statement with an error**, not silently copy the table, and not perform an instant metadata change. It must not be merged as written.

## Evidence

**1. The operation is a column type change, not a metadata-only change.**

From `migration.sql`:
```sql
ALTER TABLE `invoices`
  MODIFY COLUMN `currency` VARCHAR(3) NOT NULL,
  ALGORITHM=INSTANT, LOCK=NONE;
```
`MODIFY COLUMN` changing `CHAR(3)` → `VARCHAR(3)` is a change of data type family, not a default-value, column-order, or NULL-ability change. MySQL's documented set of INSTANT-capable `ALTER TABLE` operations does not include changing a column's type. The "metadata change" framing is incorrect: the storage representation of the column changes.

**2. MySQL rejects unsupported algorithm requests rather than falling back.**

When the requested algorithm is not supported for the operation, MySQL 8.0 errors out (e.g. `ER_ALTER_OPERATION_NOT_SUPPORTED` / "ALGORITHM=INSTANT is not supported for this operation. Try ALGORITHM=COPY"). It does **not** silently downgrade to a copying algorithm. This is the opposite failure mode from "the table gets copied while you think it's instant" — here the deploy simply breaks.

**3. The deploy is a single blocking step with no pre-validation.**

From `context.md`:
> "No staging test of this specific ALTER has been run."
> "The deploy runs migrations as a single step before starting new tasks."

Because migrations run as one step before new tasks start, this failure aborts the whole deploy and, depending on Laravel's migration runner state, can leave the schema/migration ledger in a half-applied state that requires manual intervention during a hot write window (~40 inserts/sec). That is the concrete production risk.

**4. The change is motivated by "consistency," not a data requirement.**

From `context.md`:
> "`invoices.currency` is `CHAR(3)` and we want it `VARCHAR(3)` for consistency."

There is no functional need. Per `application-usage.md`, `currency` is:
- not indexed, not part of any FK, no generated columns depend on it;
- written as always a 3-letter ISO code (`CreateInvoice.php:55`, `HistoricalInvoices.php:38`);

`CHAR(3)` already stores exactly that. The migration buys styling consistency at the cost of an 18M-row rebuild.

## If the change is still desired (correct path)

Two viable options, both requiring explicit, tested planning:

**Option A — `ALGORITHM=COPY`/`INPLACE` rebuild (not online-for-free).**
```sql
ALTER TABLE `invoices`
  MODIFY COLUMN `currency` VARCHAR(3) NOT NULL,
  ALGORITHM=COPY, LOCK=SHARED;
```
- This permanently rebuilds the table (~18M rows), builds a new table and copies data.
- `LOCK=SHARED` permits reads but blocks **writes** for the duration — unacceptable on a 40 inserts/sec hot path without a maintenance window or a tool like `gh-ost` / `pt-online-schema-change`.
- `LOCK=NONE` is not compatible with a full COPY rebuild for this operation; don't assume it works.

**Option B — Don't do it.** Given the usage evidence (constant-length ISO codes, no index/FK dependence), leave `CHAR(3)` in place. This is the lowest-risk outcome.

**Option C — Online schema-change tool.** If Option A's locking is unacceptable and the change is truly required, run it through a shadow-table tool with throttling and cut-over validation, outside the migration step, and gate it behind a separate change with staging validation.

## Pre-merge requirements

1. **Do not merge `migration.sql` as written** — it fails hard.
2. If proceeding, replace with an algorithm the operation actually supports (or use an OSC tool) and state the real locking profile.
3. Run the exact ALTER against a staging instance with realistic row counts and measure lock time before scheduling in production.
4. Confirm with the partner accounting system (the "not searched" external consumer of invoice PDFs) that the stored value is byte-identical — `CHAR(3)` right-pads and `VARCHAR(3)` does not; trimmed ISO codes should match, but verify the PDF path (`pdf.blade.php:41`) does not depend on trailing-pad behavior.

**Bottom line:** `ALGORITHM=INSTANT` does not apply to a column type change. MySQL will reject this statement; the deploy will fail. The change should either be dropped (recommended) or re-planned as a real, staged rebuild/OSC with its associated locking and validation.