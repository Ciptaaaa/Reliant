# Migration Guard Report

## Migration under review
`database/migrations/2026_11_16_124000_invoices_currency_varchar.php` (raw SQL form):

```sql
ALTER TABLE `invoices`
  MODIFY COLUMN `currency` VARCHAR(3) NOT NULL,
  ALGORITHM=INSTANT, LOCK=NONE;
```

## Target
- `invoices.currency`: `CHAR(3) NOT NULL` → `VARCHAR(3) NOT NULL`
- Table: `invoices`, InnoDB, MySQL 8.0.36, ~18,400,000 rows, ~40 inserts/sec at peak.

## Findings

### 1. Blocking — `ALGORITHM=INSTANT` is not supported for this operation
A column **type change** (`CHAR(3)` → `VARCHAR(3)`) is not in the set of instant-DDL-eligible operations. It is not a metadata-only change (the keyword being present does not make it so).

```sql
MODIFY COLUMN `currency` VARCHAR(3) NOT NULL,
ALGORITHM=INSTANT, LOCK=NONE;
```

Because an explicit `ALGORITHM=` is supplied and the operation cannot satisfy it, MySQL **rejects the statement with an error**. It does **not** silently fall back and it does **not** silently copy the table.

Consequence under the deployment facts ("the deploy runs migrations as a single step before starting new tasks"):

> The migration fails → deploy halts. New/old tasks are not rolled, and the schema change never lands. The failure mode is a failed deploy, not a long lock — but it is a guaranteed failure, not a "maybe".

### 2. The premise in context.md is wrong
> "*It is a metadata change, it should be instant.*"

`CHAR(3)` → `VARCHAR(3)` changes the stored representation (fixed-width vs variable-length, and length-byte accounting differs for the character set). It is not a metadata-only edit such as renaming or reordering. So `INSTANT` does not apply.

### 3. Correct fallback is a rebuild/copy — with the associated lock
Removing the explicit `ALGORITHM=INSTANT` lets MySQL choose. For this table the realistic path is a **table copy / online rebuild**, not an in-place change:
- `LOCK=NONE` is acceptable to request (online DDL is supported for many `MODIFY COLUMN` operations on InnoDB), but it must be **verified**, not assumed — DDL that needs a copy can still allow concurrent DML under `LOCK=NONE` in modern InnoDB, but it incurs rebuild I/O, temporary disk (~table size), and longer execution on 18M rows.
- If `LOCK=NONE` cannot be honored, MySQL errors out again rather than downgrading the lock. Do not deploy a variant that relies on a silent downgrade.

**Do not** claim "the table will be copied automatically" if `INSTANT` is kept — it will error out. **Do not** claim the operation is instant because the keyword is present — it is rejected.

### 4. Second issue — CHAR vs VARCHAR is not a no-op for indexes/columns (checked, clean here)
Per application-usage.md, this is low risk:
- "`currency` is not indexed and not part of any FK. No generated columns depend on it." → no dependent index/FK rebuild.
- Writes always produce 3-letter ISO codes (`CreateInvoice.php:55`; `HistoricalInvoices.php:38` validates 3 chars), and the schema notes "currency holds only ISO-4217 codes (3 chars) in every row." → no truncation risk.

So the *semantic* change is benign; the *operational* change is what fails.

### 5. Application compatibility
Reads treat `currency` as a string (`InvoiceResource.php:22`, `pdf.blade.php:41`) and the wire format is unchanged (3 chars either way). No app code change is required. **Not searched:** the external partner accounting system that receives PDFs — outside this repo, cannot be verified. Since the emitted value is unchanged (still 3 characters), no downstream format change is expected, but that is an assumption, not evidence.

## Verdict

**BLOCK — do not merge as written.**

The `ALGORITHM=INSTANT` clause is invalid for `MODIFY COLUMN ... VARCHAR(3)` on this table and will cause MySQL to **reject the statement**, failing the deploy. The developer's claim that this is a metadata-only instant change is incorrect.

Required changes before merge:
1. Drop `ALGORITHM=INSTANT`.
2. Decide and validate the fallback explicitly — expect a **rebuild/copy with associated locking and disk I/O** on 18M rows, not an instant metadata change.
3. **Test the exact ALTER on staging** (deployment facts: "No staging test of this specific ALTER has been run") and record the observed `ALGORITHM`/`LOCK` outcome.
4. Plan for a rolling deploy that does not require a single-step migration to succeed while the table is hot; consider a shadow-column/backfill or a maintenance-window approach if `LOCK=NONE` is not honored.
5. Confirm with the external partner accounting system (not in repo) that the emitted 3-character currency is unchanged — expected, but unverified.

## Evidence quoted
- Migration: `MODIFY COLUMN \`currency\` VARCHAR(3) NOT NULL, ALGORITHM=INSTANT, LOCK=NONE;`
- Context: "*It is a metadata change, it should be instant.*" and "The deploy runs migrations as a single step before starting new tasks."
- Schema: "Rows: ~18,400,000. Inserts: ~40/second at peak." / "currency holds only ISO-4217 codes (3 chars) in every row."
- Usage: "always writes a 3-letter ISO currency"; "validated to be 3 characters before insertion"; "`currency` is not indexed and not part of any FK. No generated columns depend on it."; "Not searched — Partner accounting system that receives invoice PDFs (external)."
- Context (case statement): "*When the requested algorithm is not supported, MySQL rejects the statement … the fallback is a rebuild/copy … must not claim the operation is instant because the keyword is present … avoid the opposite error: claiming the table will be silently copied (it will not — the statement errors out).*"