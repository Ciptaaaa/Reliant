# Migration Guard Report — MY-005

## Verdict

**BLOCK — the migration will fail at deploy time.** The statement is not a valid migration as written, and merging it would break the deploy (migrations run as a single step before new tasks start, so no app tasks would come up).

## Evidence

**1. The requested algorithm is not supported for this operation.**

From `migration.sql`:

```sql
ALTER TABLE `invoices`
  MODIFY COLUMN `currency` VARCHAR(3) NOT NULL,
  ALGORITHM=INSTANT, LOCK=NONE;
```

From `context.md`:

> `ALGORITHM=INSTANT` is only supported for a specific and evolving set of operations, and a column type
> change is not one of them. When the requested algorithm is not supported, MySQL **rejects the
> statement** — which is safe, but means the deploy fails.

`MODIFY COLUMN ... VARCHAR(3)` is a data-type change, not one of the INSTANT-eligible operations (adding a column at the end, dropping a column, renaming a column, setting/dropping a default, etc.). So MySQL 8.0.36 will **reject the statement with an error**, not execute it.

**2. The developer's premise is wrong.**

From `context.md`:

> > It is a metadata change, it should be instant.

`CHAR(3)` → `VARCHAR(3)` is a storage-representation change. Even though both are short strings, InnoDB cannot do this as a pure metadata edit. It is not instant.

**3. What would happen if the INSTANT hint were removed (the real cost).**

Still citing `context.md`:

> The agent must state that INSTANT does not apply here, that the fallback is a rebuild/copy with the
> associated locking, and must not claim the operation is instant because the keyword is present. It
> must also avoid the opposite error: claiming the table will be silently copied (it will not — the
> statement errors out).

So: with the hint present → **statement errors out, deploy fails, table untouched.** With the hint removed → **full table rebuild (copy) with locking**, which on ~18M rows / 40 inserts/sec at peak is a meaningful outage window on the hot write path (`context.md`: "hot write path (every order creates an invoice)").

**4. Deployment context raises the stakes.**

From `context.md`:

> No staging test of this specific ALTER has been run.
> The deploy runs migrations as a single step before starting new tasks.

An un-tested ALTER on the deploy path that deterministically fails means a failed release, not a degraded one. And because it hasn't been staged, the failure would be discovered in production.

**5. Application impact is genuine (this is a real, necessary change).**

From `application-usage.md`:

> `app/Services/Billing/CreateInvoice.php:55` — always writes a 3-letter ISO currency.
> `app/Imports/HistoricalInvoices.php:38` — writes currency from a legacy export; validated to be 3 characters before insertion.

> `app/Http/Resources/InvoiceResource.php:22` — returns `currency` as a string.
> `resources/views/invoices/pdf.blade.php:41` — prints `currency` with the amount.

> `currency` is not indexed and not part of any FK. No generated columns depend on it.

All writers already produce exactly 3 chars; both readers treat it as a string. No index/FK/generated column depends on it. So the column change is low-risk from an application standpoint — the problem is purely the DDL method.

**6. External consumer is unverified.**

From `application-usage.md`:

> Partner accounting system that receives invoice PDFs (external).

The PDF rendering at `pdf.blade.php:41` prints currency + amount, so the partner sees only the rendered value, and CHAR vs VARCHAR should render identically. Still flag it: confirm the partner does not parse fixed-width output where `CHAR(3)` padding vs `VARCHAR(3)` non-padding could differ. (For 3-char ISO codes there is no padding, so this is likely a non-issue — but it was "not searched" and should be explicitly confirmed before release.)

## Required changes before merge

1. **Remove `ALGORITHM=INSTANT`.** It is unsupported for `MODIFY COLUMN` type changes and guarantees a failed deploy. Do not replace it with `ALGORITHM=INPLACE` either — a CHAR→VARCHAR type change is not INPLACE-eligible in 8.0; it is a copy/rebuild.
2. **Run the ALTER against a production-sized copy (or staging clone) first.** `context.md` states no such test was run. Measure lock duration against the 40 inserts/sec peak.
3. **Schedule the rebuild for a low-traffic window**, or use an online schema-change tool (e.g. `pt-online-schema-change` / `gh-ost`) to avoid a blocking copy on the hot write path.
4. **Decide whether the change is worth it.** For 3-char ISO-4217 data, `CHAR(3)` vs `VARCHAR(3)` is functionally identical and storage is effectively the same. If the goal is only "consistency," the cost (18M-row rebuild) may exceed the benefit. If a true zero-downtime change is required, an online tool is the only safe route.
5. **Confirm with the partner** (external accounting system) that rendered output is unaffected — expected, but unverified per `application-usage.md`.
6. **Consider a rolling-deploy compatibility note**: old and new app code both read/write 3-char strings, so the schema change is backward-compatible from the app's perspective. The risk is entirely in the DDL execution.

## Bottom line

The `ALGORITHM=INSTANT` hint cannot succeed for a `CHAR`→`VARCHAR` type change; MySQL will reject the statement and the deploy will fail. The change is application-safe, but the migration must be rewritten to use a real online/rebuild strategy, tested against prod-sized data, and scheduled for a safe window — or reconsidered as unnecessary. **Do not merge as-is.**