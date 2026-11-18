# Migration Guard Report — MY-001

**Migration:** `database/migrations/2026_11_16_120000_drop_supplier_contact_phone.php`
```sql
ALTER TABLE `suppliers` DROP COLUMN `contact_phone`;
```

**Target:** `suppliers.contact_phone` (VARCHAR(32) NULL; ~4,800 rows, ~3,100 non-NULL)

## Verdict

**BLOCKER — do not merge.**

The column is still read and written by deployed application code on every runtime and scheduled path. Dropping it breaks the supplier controller, the purchase-order PDF, and the nightly ERP export the moment the DDL commits — and MySQL DDL cannot be rolled back.

## Evidence

### 1. Writes: the column is in the fillable list and validated as input

- `app/Models/Supplier.php:21`
  > `protected $fillable = ['name', 'contact_email', 'contact_phone'];`
- `app/Http/Controllers/SupplierController.php:44`
  > `$validated = $request->validate(['name' => 'required', 'contact_phone' => 'nullable|string|max:32']);`

Creating or updating a supplier will attempt to persist `contact_phone` into a column that no longer exists → "Unknown column" error on the write path.

### 2. Reads: the column is rendered on customer-facing output

- `resources/views/purchase-orders/pdf.blade.php:63`
  > `{{ $supplier->contact_phone ?? '—' }}`
  > → printed on every purchase order PDF.

The view reads the attribute off the model. After the drop, the hydrated model has no `contact_phone` attribute. Depending on `Supplier::$fillable`/casts and Eloquent behavior this yields at best a silent `—` and at worst a model not-found/missing-attribute path. Either way, purchase order PDFs lose the supplier phone with no error surfaced — a silent data-correctness regression on documents sent to customers.

### 3. Reads: the nightly ERP export queries the column by name

- `app/Services/ErpExport.php:37`
  > `DB::table('suppliers')->select('id', 'name', 'contact_phone')->whereNotNull('contact_phone')->chunk(...)`
  > → nightly ERP export, runs as a scheduled command using the deployed code image.

This is a **hard failure**: `select('... contact_phone')` on a dropped column is a SQL error. The export command will fail every night until the code is changed and redeployed. The external ERP consumer (explicitly *not searched*) receives stale or missing data until someone notices.

### 4. The "small table, lock is fine" framing does not apply

The developer's rationale — *"It is a small table so the lock should be fine"* — addresses metadata-lock duration, which is indeed trivial at 4,800 rows. It is the wrong axis of risk. The hazard here is **schema/code incompatibility**, not lock contention:

- MySQL 8.0 DDL is atomic and **committed immediately** (`context.md`: "MySQL DDL does not roll back; if the statement succeeds it is committed immediately").
- The ECS rolling update runs **old and new tasks simultaneously** (`context.md`). During the overlap, old tasks — running the code image above — will hit the dropped column. There is no deploy ordering that makes this safe, because the column is used by the currently deployed code, not just the new code.
- Once the DDL commits, the only recovery is **backward migration (re-add column) + restore of data**, not transaction rollback.

## Blast radius

| Path | Trigger | Effect |
|---|---|---|
| Supplier create/update | every write to a supplier | error (unknown column) |
| PO PDF | every purchase order | supplier phone silently lost / error |
| ERP export | nightly cron | scheduled command fails; ERP starved of data |
| Seeder | local/dev only | non-production |

## Required before merge

1. **Remove all code references first and verify.** Update `Supplier::$fillable`, the controller validation, the PDF view, and `ErpExport` to stop using `contact_phone`. Deploy and confirm the old code is fully drained from ECS (both tasks).
2. **Migrate or confirm the data destination.** If the ERP still needs phone data, populate it from the survivor field before the drop; do not silently lose ~3,100 populated values.
3. **Then sequence the drop in a separate release**, after the code referencing the column has been gone for at least one deploy cycle — never in the same rolling update.
4. **Re-verify** after the code change: grep the deployed image (not just the repo) for `contact_phone`, including any external ERP consumer contract, before treating the column as unused.

## Note on backup window

Point-in-time recovery (7 days) allows restoring data if the drop is executed and someone needs the values back — but it does **not** make the migration safe to merge, and the ERP/PDF failures would occur before anyone restored. The backup is a recovery backstop, not a substitute for code-first sequencing.