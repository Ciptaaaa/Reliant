# Migration Guard Report — MY-001

**Migration:** `database/migrations/2026_11_16_120000_drop_supplier_contact_phone.php`
```sql
ALTER TABLE `suppliers` DROP COLUMN `contact_phone`;
```
**Verdict: 🔴 BLOCKER — do not merge.**

---

## Summary

The `DROP COLUMN` is mechanically safe (tiny table, brief lock), but it removes a column the **deployed application code actively reads and writes**, including on a nightly scheduled job and on customer-facing purchase order PDFs. Old app tasks will keep running for the duration of the rolling update and will fail as soon as the column disappears. The user's "it has not been used since we switched to a supplier contact form" claim is contradicted by the code. This is a correctness/outage blocker, not a lock-risk question.

---

## Evidence

### 1. Model still treats the column as mass-assignable
`app/Models/Supplier.php:21`:
```php
protected $fillable = ['name', 'contact_email', 'contact_phone'];
```
Any create/update via the model that includes `contact_phone` will throw `SQLSTATE[42S22] Column not found` once dropped.

### 2. Controller still validates/accepts the field
`app/Http/Controllers/SupplierController.php:44`:
```php
$validated = $request->validate(['name' => 'required', 'contact_phone' => 'nullable|string|max:32']);
```
Validation still passes the field through to persistence (fillable above), so supplier edits will 500 after the drop.

### 3. Purchase order PDFs read it on every render
`resources/views/purchase-orders/pdf.blade.php:63`:
```
{{ $supplier->contact_phone ?? '—' }}
```
If the column is absent, the attribute lookup returns null and **silently prints "—" on every PO PDF** rather than failing loudly — a quiet data-loss/regression on a business document. Either way the intended phone number is gone.

### 4. Nightly ERP export selects the column explicitly
`app/Services/ErpExport.php:37`:
```php
DB::table('suppliers')->select('id', 'name', 'contact_phone')->whereNotNull('contact_phone')->chunk(...)
```
> "nightly ERP export, runs as a scheduled command using the deployed code image."

This is a bare query-builder `select` of `contact_phone` — it will hard-fail (`Unknown column 'contact_phone'`) the first night after deploy until the new code image is live, and the export feeds an **external ERP** (per Application usage → Not searched: "The ERP system itself (external; consumes the nightly export file)"). A failed/short export silently starves the ERP.

### 5. Rolling deploy means the column cannot be removed in one step
`context.md` / Deployment facts:
> "Old and new application tasks run simultaneously during the rolling update."
> "MySQL DDL does not roll back; if the statement succeeds it is committed immediately."

There is no window where *all* running code has stopped touching `contact_phone`. The drop commits instantly and irreversibly; any old task (or the scheduled export running during the rollout) hits the failures in items 1–4. **~3,100 of ~4,800 rows** hold a non-NULL value — that is real production data being destroyed, not an empty column, and PITR (7-day) is the *only* recovery path, requiring a full restore.

The developer's premise — "has not been used since we switched to a supplier contact form" — is **false** against the current code image. "Small table" is irrelevant to this risk.

---

## What would make this safe (required before approval)

1. **Remove all code references first and deploy them**, then drop the column in a *later* migration. Minimum set:
   - `Supplier.php` `$fillable` — remove `contact_phone`
   - `SupplierController.php:44` — remove from validation (and any persistence of it)
   - `purchase-orders/pdf.blade.php:63` — replace with the new contact-form source, confirm PDF output with stakeholders
   - `ErpExport.php:37` — drop `contact_phone` from the select and confirm the external ERP no longer expects that field in the export file
   - `SupplierSeeder.php:15` — remove the seeded value
2. **Decide the fate of the existing 3,100 values** before the drop: migrate `contact_phone` into the new "supplier contact form" storage (backfill), or obtain explicit sign-off that the data is disposable. Do not rely on PITR as the primary safety net.
3. **Confirm the external ERP contract** — the export file's schema is outside this repo; a column disappearing from the nightly file can break the ERP downstream. This is an unverified dependency (Application usage → Not searched).
4. **Sequencing:** ship code-removal in release N, verify no code path reads the column, *then* drop in release N+1. Given rolling deploys, a same-release drop is inherently unsafe.

---

## Verdict

**BLOCKER.** The migration drops a column that the deployed code reads/writes on the supplier model, supplier edit controller, every purchase-order PDF, and a nightly ERP export feeding an external system — while a rolling deploy guarantees old code is still running when the DDL commits irreversibly. Do not merge. Remove the application usage first, handle the ~3,100 existing values, verify the ERP export contract, and return with a follow-up migration after the code-removal release.