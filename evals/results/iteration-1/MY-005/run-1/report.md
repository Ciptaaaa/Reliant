# Migration Guard Report — MY-005

**Migration:** `database/migrations/2026_11_16_124000_invoices_currency_varchar.php` (raw SQL form)
**Table:** `invoices` (~18,400,000 rows; ~40 inserts/s at peak)
**Statement:**
```sql
ALTER TABLE `invoices`
  MODIFY COLUMN `currency` VARCHAR(3) NOT NULL,
  ALGORITHM=INSTANT, LOCK=NONE;
```

**Verdict:** ❌ **BLOCK / DO NOT MERGE.** The statement will be **rejected by MySQL** with an error. This is not an instant metadata change, and it is not a silent table copy — it fails outright and takes the deploy step down with it.

---

## 1. Core finding: `ALGORITHM=INSTANT` is not supported for this operation

Changing a column's data type (`CHAR(3)` → `VARCHAR(3)`) is a **table-rebuild operation**, not a metadata-only change. In MySQL 8.0, `ALGORITHM=INSTANT` is limited to a specific, evolving allow-list of operations (adding a column at the end, dropping a column [8.0.29+], changing a column default, ENUM/SET value append, etc.). A column type change is **not** on that list.

Per MySQL's documented behavior for explicitly specified algorithms: *"If you specify `ALGORITHM=INSTANT` and the operation does not support it, the statement fails with an error."* MySQL does **not** silently downgrade an explicitly requested algorithm.

So the developer's premise in `context.md` —
> *"It is a metadata change, it should be instant."*

— is **false**, and the requested algorithm makes the situation worse, not better: it guarantees an error instead of performing the rebuild.

### Evidence from the schema
`schema.sql` shows the current definition:
```sql
`currency` CHAR(3) NOT NULL,
```
Changing the type of an existing column is a change to the row storage format, which requires rebuilding the clustered index. This is the canonical case where INSTANT is unsupported.

---

## 2. Why it fails rather than copies

The case notes warn against the "opposite error" (claiming a silent copy). To be explicit:

- Because **`ALGORITHM=INSTANT` is stated explicitly**, MySQL rejects the statement. The table is **not** rebuilt and **not** copied — the deploy fails at the migration step.
- Per `context.md`: *"The deploy runs migrations as a single step before starting new tasks."* A failed migration means **new task startup does not begin** — the release halts. With the app on a rolling deploy and a hot write path (`~40 inserts/s at peak`), a failed deploy step is a **release-blocking incident**, not a data-corruption risk.

**Reference behavior if the algorithm were omitted or `INPLACE`/`COPY` were requested:**
- `ALGORITHM=INPLACE, LOCK=NONE` is **not** generally permitted for `CHAR`→`VARCHAR` type changes; MySQL would reject *that* too in most 8.0.x versions for this specific change.
- The realistic fallback is **`ALGORITHM=COPY`** (or an in-place rebuild that permits `LOCK=SHARED`), which **blocks writes** for the duration of an ~18.4M-row table rebuild — unacceptable on a hot write path without a dedicated maintenance window or online-schema-change tooling.

> Note: I am flagging this as the *likely* fallback, but since `context.md` states *"No staging test of this specific ALTER has been run,"* the exact permissible algorithm on this build must be verified before choosing a path.

---

## 3. Application impact review

From `application-usage.md`:

- **Writes:** `CreateInvoice.php:55` writes 3-letter ISO; `HistoricalInvoices.php:38` writes 3 chars validated. → `VARCHAR(3) NOT NULL` is **type-compatible with all write paths.** ✔
- **Reads:** `InvoiceResource.php:22` and `pdf.blade.php:41` treat currency as a string. → **No code change required** for `VARCHAR`. ✔
- **Indexes/constraints:** *"`currency` is not indexed and not part of any FK. No generated columns depend on it."* → No index/FK/generated-column rebuild side effects. ✔
- **Not searched:** *"Partner accounting system that receives invoice PDFs (external)."* → ⚠ **Unverified external consumer.** PDF output is a string of ISO codes under both `CHAR` and `VARCHAR`, so this is low risk, but it is explicitly **not confirmed** and must be owner-signoff'd before merge.

**Conclusion:** the *target type* (`VARCHAR(3)`) is reasonable and application-safe. The problem is exclusively the **algorithm and the missing validation**, not the intent.

---

## 4. Required changes before merge

1. **Remove `ALGORITHM=INSTANT`.** It cannot succeed for this operation.
2. **Determine the actual supported algorithm** by running the exact `ALTER` (without the algorithm clause, or with the algorithm you intend) against a **staging copy at production row count** (~18.4M rows). Do not guess.
3. **If a locking rebuild is unavoidable**, plan it as a maintenance-window operation (or use `pt-online-schema-change` / `gh-ost`) — **not** a single-step pre-deploy migration.
4. **Obtain sign-off from the partner accounting system owner** that `VARCHAR(3)` vs `CHAR(3)` in generated PDFs is acceptable (low risk; currently unverified).
5. **Re-run Migration Guard** on the corrected statement before merge.

---

## 5. Summary table

| Check | Result |
|---|---|
| `ALGORITHM=INSTANT` valid for CHAR→VARCHAR? | ❌ No — operation not on INSTANT allow-list |
| Statement outcome with these flags | ❌ **Errored / rejected**, not executed |
| Silent table copy risk | ❌ Not applicable — statement fails first |
| Fallback if flags corrected | Rebuild; likely COPY / `LOCK=SHARED`; ~18.4M rows blocked |
| App writes compatible with VARCHAR(3)? | ✅ Yes |
| App reads compatible? | ✅ Yes |
| Index/FK/generated column impact | ✅ None (`currency` unindexed, not an FK) |
| Staging-tested? | ❌ No (`context.md`) |
| External PDF consumer verified? | ⚠ Not searched / unverified |
| **Verdict** | **BLOCK** |

**Do not merge.** The migration as written is guaranteed to fail and halt the deploy; it must be replaced with a staged, validated plan.