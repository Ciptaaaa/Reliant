# Migration Guard Report — MY-001

**Migration:** `database/migrations/2026_11_16_120000_drop_supplier_contact_phone.php`
```sql
ALTER TABLE `suppliers` DROP COLUMN `contact_phone`;
```

## Verdict: **BLOCKER** — do not merge

The column is still read by deployed code in three places, one of which runs on a schedule against the deployed code image. Dropping it commits immediately (MySQL DDL does not roll back) and will break the purchase-order PDF and the nightly ERP export during and after the rolling update, with no code rollback path that restores the data.

## Evidence

### 1. Column is in active use by deployed code

| Site | Evidence | Impact of drop |
|---|---|---|
| `app/Models/Supplier.php:21` | `protected $fillable = ['name', 'contact_email', 'contact_phone'];` | Mass-assignment silently drops the attribute; writes/reads via model fail or lose data. |
| `app/Http/Controllers/SupplierController.php:44` | `$validated = $request->validate(['name' => 'required', 'contact_phone' => 'nullable\|string\|max:32']);` | Validation passes but persisting to a dropped column errors. |
| `resources/views/purchase-orders/pdf.blade.php:63` | `{{ $supplier->contact_phone ?? '—' }}` → "printed on every purchase order PDF." | Every PO PDF loses the phone (or errors, depending on attribute access). Business-facing. |
| `app/Services/ErpExport.php:37` | `DB::table('suppliers')->select('id', 'name', 'contact_phone')->whereNotNull('contact_phone')->chunk(...)` → "nightly ERP export, runs as a scheduled command using the deployed code image." | Export query fails on missing column → nightly job errors; ERP stops receiving supplier phone data. |

The developer's claim that the column "has not been used since we switched to a supplier contact form" is contradicted by the code above.

### 2. Rolling deploy guarantees a mixed old/new window

From `context.md`: "Old and new application tasks run simultaneously during the rolling update." There is no two-phase path here — the moment the migration commits, **both** old tasks (which still `SELECT contact_phone`) and the scheduled `ErpExport` (which uses the deployed code image) hit a column that no longer exists.

### 3. DDL is irreversible and the data is not recoverable by code rollback

From `context.md`: "MySQL DDL does not roll back; if the statement succeeds it is committed immediately." Backups exist (7-day PITR, 5-min RPO), but you cannot restore just the column without a full restore to a point before the DDL, and the ~3,100 non-NULL values (per `schema.sql`: "About 3,100 rows have a non-NULL `contact_phone`") are lost from the primary the instant the statement runs. Rolling back the *application* does nothing to bring the column back.

### 4. "Small table" is not the risk here (rejecting the framing)

`context.md` explicitly flags this: "The `DROP COLUMN` is fast and the lock is trivial on 4,800 rows, so lock analysis alone would call this safe. It is a `BLOCKER` because the code still uses the column." The lock cost is indeed negligible; the correctness cost is total. The table size and the existing index `suppliers_contact_phone_idx` are not the deciding factors.

### 5. Downstream consumer not fully analyzed

`application-usage.md` notes: "Not searched: The ERP system itself (external; consumes the nightly export file)." Even if the internal code is fixed, the external ERP's contract with the export file (presence of the phone column) must be confirmed before any drop. This is a prerequisite, not a residual risk.

## Required before this can proceed

1. **Remove all code references first** in a prior deploy: model `$fillable`, controller validation, Blade template (use a placeholder or a different field), and `ErpExport.php` (stop selecting the column). Ship and verify that deploy is fully live.
2. **Confirm the external ERP** no longer depends on the phone field in the nightly export file.
3. **Export/back up the data**: snapshot the ~3,100 non-NULL values (e.g., `CREATE TABLE suppliers_contact_phone_backup AS SELECT id, contact_phone FROM suppliers WHERE contact_phone IS NOT NULL;`) so the values can be restored if a rollback is needed.
4. **Only then** run the `DROP COLUMN` in a later migration, after a full deploy cycle, and keep the backup table.

## Summary

| Check | Result |
|---|---|
| Column referenced by deployed code | **Yes — 4 sites incl. scheduled ERP export** |
| Rolling-deploy mixed-version window | Yes — old and new tasks coexist |
| DDL reversible / data recoverable | No — committed immediately, only full PITR restore |
| Lock / table-size risk | Low (as developer said) — not the blocker |
| External consumer verified | Not searched — must be confirmed |

**BLOCKER.** The migration drops a column that is live in the deployed code and the nightly ERP export. It must be split into a remove-code-first phase plus a dat

**Report format note:** I have followed the structure specified by the skill (verdict, evidence, required actions, summary).