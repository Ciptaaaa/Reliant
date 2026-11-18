# Migration Guard Report

## Verdict: **BLOCKER**

## Summary

`migration.sql` drops `suppliers.contact_phone`, but the deployed application code still **reads and writes** this column in at least three places, including a nightly ERP export that runs against the live database. The migration is not rollback-safe (MySQL DDL commits immediately) and unsynchronized with the rolling deployment, so it breaks both currently running and incoming application tasks.

## Evidence

**1. The migration drops a live column.**
```sql
ALTER TABLE `suppliers` DROP COLUMN `contact_phone`;
```
This removes both the column and its index `suppliers_contact_phone_idx` immediately and irreversibly on MySQL.

**2. Active write path via Eloquent mass assignment.**
`app/Models/Supplier.php:21`:
```php
protected $fillable = ['name', 'contact_email', 'contact_phone'];
```
With the column gone, any create/update that includes `contact_phone` produces a SQL error (`Unknown column 'contact_phone'`) or a `QueryException` — writing to a non-existent attribute on a `fillable` model.

**3. Active validation/read path in the controller.**
`app/Http/Controllers/SupplierController.php:44`:
```php
$validated = $request->validate(['name' => 'required', 'contact_phone' => 'nullable|string|max:32']);
```
The validator still accepts and surfaces `contact_phone`; downstream persistence will fail.

**4. Active read path on every purchase order PDF.**
`resources/views/purchase-orders/pdf.blade.php:63`:
```
{{ $supplier->contact_phone ?? '—' }}
```
Rendering this view after the drop raises an attribute/column error (or silently renders the fallback if the model tolerates missing attributes — either way, phone numbers printed on POs disappear). This is a **material business impact** on the deployed artifact.

**5. Scheduled ERP export selects the column directly.**
`app/Services/ErpExport.php:37`:
```php
DB::table('suppliers')->select('id', 'name', 'contact_phone')->whereNotNull('contact_phone')->chunk(...)
```
This runs nightly using the **deployed code image**, outside the web request path. Post-migration, the query plans for a dropped column fail — the export breaks on the next scheduled run, before any code fix can be rolled out. The external ERP consumer's contract is also invalidated (external system not searched, and it doesn't need to be: the export query itself fails).

## Aggregate data impact

`schema.sql` comments confirm the column is not vestigial:
- ~4,800 rows with ~3,100 non-NULL `contact_phone`.
- "`contact_phone` is the phone number printed on purchase order PDFs and exported to the ERP nightly."

The developer's premise *"has not been used since we switched to a supplier contact form"* is **contradicted by the code**: the column is written, validated, printed on PDFs, and exported nightly. Dropping it discards ~3,100 live phone numbers with no backup path to read them back.

## Deployment / rollback analysis

- **Rolling update**: Old tasks (2) and new tasks run simultaneously. During the window where old tasks are still serving, the column exists but the **DDL is already committed**. Old task → new task transition is fine for the column's physical existence, but any request hitting the *new* image before the migration is fine too — the danger is the reverse: once the migration commits, **every** old task immediately errors on the code paths above. You cannot "roll back the migration"; you can only roll back code, and stale tasks may still be mid-flight.
- **MySQL DDL does not roll back.** A failed statement is fine (nothing changes), but a successful `DROP COLUMN` is committed synchronously and requires restoration from *re-adding the column* — and data must be recovered from PITR/backup, with a gap between the drop and the backup.
- **PITR window**: 5-minute point-in-time recovery retained 7 days. This is a *recovery* mechanism, not a *rollback* mechanism; it requires a restore operation with data loss between the last backup and the drop.

The "small table / lock is fine" framing answers the wrong question. Lock duration is negligible here; **the risk is semantic, not mechanical**, and the 2-task rolling update plus `ErpExport.php` still reading via raw `DB` means the correctness break is automatic and immediate.

## Recommended remediation

Either path works, but they must not be combined in one step:

1. **Code-first, then column (preferred):**
   - Ship a release that stops using `contact_phone` (drop from `$fillable`, controller validation, PDF view, `ErpExport.php`, and update the ERP consumer contract).
   - Wait for one full nightly export cycle to complete on the new image *and* for the rolling update to fully drain old tasks.
   - **Then** add the `DROP COLUMN` migration.

2. **Keep the column** if the ERP consumer still needs phone numbers until its feed changes; add a **new** optional `contact_form` table/column and migrate data safely, then drop only after the external ERP confirms the field is no longer required.

**Do not merge the provided `migration.sql` in its current form.**

## Evidence gaps

- ERP system itself was not searched (stated as external) — but this doesn't affect the verdict: `ErpExport.php:37` selects the column directly, so the export query fails regardless of what the ERP accepts.
- Seeder reference (`SupplierSeeder.php:15`) only impacts local/dev and is not a production risk on its own.

**Verdict: BLOCKER — the migration removes a column that the deployed code still writes, reads in the PO PDF, and selects in the nightly ERP export.**