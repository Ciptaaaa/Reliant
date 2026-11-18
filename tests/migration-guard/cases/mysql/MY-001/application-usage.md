# Application usage: `suppliers.contact_phone`

## Reads / writes on the deployed code

- `app/Models/Supplier.php:21`
  ```php
  protected $fillable = ['name', 'contact_email', 'contact_phone'];
  ```
- `app/Http/Controllers/SupplierController.php:44`
  ```php
  $validated = $request->validate(['name' => 'required', 'contact_phone' => 'nullable|string|max:32']);
  ```
- `resources/views/purchase-orders/pdf.blade.php:63`
  ```
  {{ $supplier->contact_phone ?? '—' }}
  ```
  → printed on every purchase order PDF.
- `app/Services/ErpExport.php:37`
  ```php
  DB::table('suppliers')->select('id', 'name', 'contact_phone')->whereNotNull('contact_phone')->chunk(...)
  ```
  → nightly ERP export, runs as a scheduled command using the deployed code image.

## Other references

- `database/seeders/SupplierSeeder.php:15` seeds `contact_phone` in local/dev environments.
- No database views or generated columns use `contact_phone`.

## Not searched

- The ERP system itself (external; consumes the nightly export file).
