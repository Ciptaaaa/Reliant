# MY-001 — Expected analysis

## Expected verdict

**BLOCKED** — the column is still used by the supplier form, the purchase-order PDF, and the nightly
ERP export.

## Findings

### F1 — `BLOCKER` — `destructive-column-drop` — dropping a column still used by application code

`suppliers.contact_phone` is referenced by:

- `app/Models/Supplier.php:21` — mass-assignable.
- `app/Http/Controllers/SupplierController.php:44` — validated on supplier create/update.
- `resources/views/purchase-orders/pdf.blade.php:63` — printed on purchase order PDFs.
- `app/Services/ErpExport.php:37` — selected by the nightly ERP export.

After the drop, the supplier form's validation/save fails (unknown column) and the PDF/ERP export
raise `SQLSTATE[42S22]: Column not found`. The ERP export runs from the **deployed image**, so it keeps
failing until a new image removes the reference.

**Evidence required:** `application-usage.md` (Supplier.php / ErpExport.php at minimum), `migration.sql`.

**Mitigation:** stop all reads/writes and migrate the ERP export to a replacement field in a release
first; then drop in a separate migration after one full deploy cycle.

**Approval: accept-risk (F1)** — a human accepts the loss of the old values; a recovery plan and restore point are required.
recovery.

### F2 — `HIGH` — `data-integrity` — irreversible data loss on MySQL

MySQL `DROP COLUMN` is immediate and cannot be rolled back inside a transaction (DDL causes an implicit
commit). About 3,100 non-NULL phone numbers are permanently removed; recovery requires RDS
point-in-time restore to a separate instance, which reverts unrelated tables to that moment (RPO up to
5 minutes, retention 7 days).

**Evidence required:** `migration.sql`, `schema.sql` (row/value estimate).

**Mitigation:** export `contact_phone` to an archive table or file inside the same change window if the
data must be preserved; otherwise record the restore point explicitly.

**Approval: accept-risk (F1)** — a human accepts the loss of the old values; a recovery plan and restore point are required.

### F3 — `INFO` — `no-risk-observed` — lock and table size

On a 4,800-row table, `DROP COLUMN` is fast and the metadata lock is brief; lock impact is negligible
**on MySQL 8.0** (MySQL 5.7 rebuilds the table — verify the MySQL version if it is not 8.0). It does
not change the verdict: the blocker is compatibility, not locking.

## Unknowns that must be reported

- Whether the ERP consumer can change before the drop (external system, not inspected).
- Seeders in dev environments will break, but that does not affect production.

## Verdict reasoning

Evidence of breakage of running code → `BLOCKER` → `BLOCKED`. "Small table so the lock is fine" is
true but irrelevant; the agent must not let the user's framing set the verdict.
