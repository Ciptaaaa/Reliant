# Migration Guard Report — MY-008

**File:** `database/migrations/2026_11_16_131500_customers_rename_mobile.php` (raw SQL form)
**Statement:** `ALTER TABLE \`customers\` RENAME COLUMN \`mobile\` TO \`mobile_phone\`, ALGORITHM=INSTANT;`

**Verdict: BLOCK — not safe to merge as-is.**

---

## 1. What the migration does

Renames `customers.mobile` → `customers.mobile_phone` on a ~620,000-row InnoDB table, using `ALGORITHM=INSTANT`.

The DDL itself is fine on this engine/version: `RENAME COLUMN` is supported in MySQL 8.0.28+, and the stated version is 8.0.36. It is an instant, metadata-only operation. Per the supplied evidence, `customers_mobile_idx` stays attached (`"RENAME COLUMN keeps the index attached (index name is unchanged), so plans are preserved."`), and there are no generated columns or FKs on `mobile`. There is no `CHANGE COLUMN`-style type/attribute drift risk here because the full column definition is not being restated.

So the engine-level claim ("no downtime / instant") is true. The problem is not the DDL. It is the **application contract**.

## 2. The real risk: a rename is a hard cut, not an additive change

The evidence shows three independent readers/writers of the **old** name that will still be live after the migration runs:

1. **Deployed web tasks (2, during rolling update).** The migration runs first (`"The migration runs before the new application tasks start."`), and `"Old tasks continue serving for several minutes during the rolling update."` Deployed code still reads `mobile`:
   - `CustomerController.php:52` → `$customer->only(['id','name','email','mobile'])`
   - `SendVerification.php:31` → `$this->sms->send($customer->mobile, $code)`
   
   After the rename, `customer->mobile` no longer exists → these old tasks start failing (undefined attribute / SQL error on any query selecting `mobile`) for the whole overlap window.

2. **Nightly cron.** `ExportCustomers.php:26` does `DB::table('customers')->select('id','name','mobile')->whereNotNull('mobile')`, and per the deployment facts it runs `"from the last released image ... keeps selecting mobile until a new image is released and the scheduler updated."` If the cron fires between migration and the new scheduler image, the export fails outright (`Unknown column 'mobile'`). This is a scheduled job, so the failure can be silent/late.

3. **Mobile app / API clients.** `mobile` is emitted in the JSON response (`"Not searched — Mobile app clients that receive mobile in the JSON response (API contract change)."`). Renaming the field is a breaking wire-format change for every client that reads `mobile`. This was explicitly not investigated, so the blast radius is unknown.

The middle one is the classic trap flagged in `context.md`: *"the agent should verify the version specific behaviour and not conflate 'instant DDL' with 'safe for a rolling deploy'."* Instant DDL only means the schema lock is short. It says nothing about whether old code still resolves the old name — and here it demonstrably does.

## 3. Why `ALGORITHM=INSTANT` does not rescue this

- It makes the **change** fast; it does not make the **rename backward compatible**. There is no aliasing: after the statement commits, `mobile` is gone for every session, including old app tasks and the stale cron image.
- The migration is not reversible in practice during the window: a rollback re-run would itself break the newly deployed tasks that already use `mobile_phone`, and re-running the rename is a second hard cut.

## 4. Required changes before merge

Adopt an **expand/contract (multi-phase)** sequence instead of a single rename:

- **Phase 1 (this migration):** add `mobile_phone` as a nullable column, backfill from `mobile` (batched, e.g. chunked on `id` given ~510k non-NULL rows), and add a trigger or dual-write in app code so writes keep both in sync. Leave `mobile` in place. Add an index on `mobile_phone` if the lookup is needed (do not rely on renaming the index).
- **Phase 2 (deploy new app):** switch controllers/services to `mobile_phone`, feature-flag via config if possible; keep `mobile` populated.
- **Phase 3 (after old tasks and the stale cron image are fully retired, and mobile clients confirmed migrated):** stop writing `mobile`, then a final migration drops `mobile` (and its index) with `ALGORITHM=INPLACE, LOCK=NONE` or documented lock budget.

Additional pre-merge checks:
- **API contract:** enumerate mobile clients consuming `mobile` in the JSON response and coordinate the field change (version or emit both fields during transition). The current "Not searched" note is a blocker, not a footnote.
- **Cron:** confirm the scheduler image is updated to the release that reads `mobile_phone` before the rename would take effect, or the expand phase makes it moot.
- **Verify the DDL claim** on a staging replica of 8.0.36 rather than trusting the comment; do not use `CHANGE COLUMN old new <definition>` (type/attribute drift trap).

## 5. Evidence relied on

- `context.md`: *"The migration runs before the new application tasks start."*, *"Old tasks continue serving for several minutes during the rolling update."*, *"runs from the last released image ... keeps selecting mobile until a new image is released and the scheduler updated."*, and the explicit warning *"do not conflate 'instant DDL' with 'safe for a rolling deploy'."*
- `application-usage.md`: deployed `CustomerController.php:52` and `SendVerification.php:31` use `mobile`; branch uses `mobile_phone`; `ExportCustomers.php:26` selects/whereNotNull `mobile`; *"Not searched — Mobile app clients that receive mobile in the JSON response (API contract change)."*
- `schema.sql`: `mobile VARCHAR(32) DEFAULT NULL`, `KEY customers_mobile_idx (mobile)`, ~620k rows / ~510k non-NULL.
- `migration.sql`: single `RENAME COLUMN ... ALGORITHM=INSTANT` with no compatibility step.

---

**Summary:** The `ALGORITHM=INSTANT` rename is technically valid and painless for the engine, but it is a **breaking change for live readers** — two deployed web tasks, the nightly export cron running from a stale image, and un-enumerated mobile API clients. Merge is blocked until this is converted to an expand/contract migration (add + backfill + dual-read/write, then drop later) and the API consumer impact is assessed.