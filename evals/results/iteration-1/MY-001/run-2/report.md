# Migration Guard Report — MY-001

**Migration:** `database/migrations/2026_11_16_120000_drop_supplier_contact_phone.php`
```sql
ALTER TABLE `suppliers` DROP COLUMN `contact_phone`;
```
**Engine:** MySQL 8.0.36, InnoDB, single primary (RDS)
**Table:** `suppliers` — ~4,800 rows, ~3,100 non-NULL `contact_phone` values

## Verdict: ⛔ BLOCKER

The migration is rejected. The column is still read and written by the code that is deployed right now, and by code that keeps running *after* the migration commits. Data loss (~3,100 values) is irreversible. The lock profile is fine — but the lock is not the risk here.

---

## Evidence

### 1. Reads — the column is selected at runtime

`app/Services/ErpExport.php:37`
```php
DB::table('suppliers')->select('id', 'name', 'contact_phone')->whereNotNull('contact_phone')->chunk(...)
```
> → nightly ERP export, runs as a scheduled command **using the deployed code image**.

`resources/views/purchase-orders/pdf.blade.php:63`
```
{{ $supplier->contact_phone ?? '—' }}
```
> → printed on **every purchase order PDF**.

After the `DROP`, both queries fail with `SQLSTATE[42S22] Unknown column 'contact_phone'`. The ERP export and the PO PDF render are core business outputs — not optional.

### 2. Writes — the column is fillable and validatable

`app/Models/Supplier.php:21`
```php
protected $fillable = ['name', 'contact_email', 'contact_phone'];
```
`app/Http/Controllers/SupplierController.php:44`
```php
$validated = $request->validate(['name' => 'required', 'contact_phone' => 'nullable|string|max:32']);
```
Supplier create/update inserts/updates `contact_phone`, so the column is live on the write path too — the developer's premise ("not used since we switched to a supplier contact form") is contradicted by the deployed controller, which *validates exactly that field*.

### 3. Rolling deployment overlap

> "Old and new application tasks run simultaneously during the rolling update."

The old tasks (ECS image before merge) still contain all four references above. MySQL DDL autocommits immediately:

> "MySQL DDL does not roll back; if the statement succeeds it is committed immediately."

So the moment the migration runs, *old-tasking* requests, the nightly export, and PO generation break — during the rollout, not after it. Even if a fix-forward deploy follows, the first old task to hit these paths fails.

### 4. Irreversible data loss

> ~3,100 rows have a non-NULL `contact_phone`.

`DROP COLUMN` deletes those values permanently. PITR (5 min, 7 days) is a forensic aid, not a migration rollback strategy for a table that is actively written.

### 5. The "small table" framing is not the risk analysis

> "It is a small table so the lock should be fine."

That is true and irrelevant: 4,800 rows makes the metadata lock negligible. The blocker is **contract compatibility**, not lock duration. Lock analysis alone would have called this safe — which is exactly why it is a BLOCKER.

---

## Required before merge

1. **Remove all code references first** (and deploy them): `ErpExport::select(...)`, `pdf.blade.php`, `$fillable`, controller validation, `SupplierSeeder`. Ship in a release that *precedes* the migration.
2. **Retire the ERP export dependency**: the export column is consumed by an external system ("Not searched... external; consumes the nightly export file"). Confirm the ERP side no longer reads that field or a replacement is in place.
3. **Roll in an expand–contract sequence**, not a single `DROP`:
   - Release N: code stops reading/writing `contact_phone`; ERP export switches to the replacement field.
   - Release N+1: migration drops the column and its index `suppliers_contact_phone_idx`.
4. **Back up the data** (`SELECT id, contact_phone INTO OUTFILE` / snapshot) before the drop, since PITR is not a reversal mechanism.
5. **Set the maintenance window / run during the gap between old-task drainage and cut-over** so no old task is live when the DDL commits.

## Note

The migration as written is unreviewable for PG-001-style reasoning: the same pattern would pass a lock-only check. The deployed code contradicts the stated justification, and the external ERP consumer is unverified — both must be closed before this can be re-reviewed.