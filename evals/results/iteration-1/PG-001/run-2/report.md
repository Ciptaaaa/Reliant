# Migration Guard Report — PG-001

## Migration under review

```sql
-- db/migrate/20261115093000_remove_phone_from_users.sql
ALTER TABLE users DROP COLUMN phone;
```

## Verdict: **BLOCK — do not merge**

The developer's premise ("it is unused") is contradicted by the attached usage report. `phone` is read, validated, exported, and used as the sole 2FA recovery channel by code that is still on `main` and **has not been deployed**. Dropping the column now will break production immediately upon deploy, irrespective of schema rollback.

---

## Evidence

### 1. `phone` is actively read/written by application code

From `application-usage.md`:

- Strong params permit it — `app/controllers/users_controller.rb:14`:
  > `permitted = params.require(:user).permit(:email, :phone, :display_name)`
- It is returned in the API — `users_controller.rb:31`:
  > `render json: @user.slice(:id, :email, :phone, :display_name)`
  > → the profile endpoint returns `phone` in the API response body.
- It is validated — `app/models/user.rb:9`:
  > `validates :phone, format: { with: /\A\+?[0-9 ]{7,20}\z/ }, allow_blank: true`
- **It is the only SMS 2FA recovery channel** — `app/services/two_factor/send_code.rb:22`:
  > `return unless user.phone.present?`
  > `SmsGateway.deliver(to: user.phone, body: code)`
  > → phone is the *only* 2FA recovery channel for SMS users.

A `DROP COLUMN` makes these code paths raise (e.g. `ActiveRecord::StatementInvalid` / `NoMethodError` on a nil/unknown attribute) the moment the code runs against the migrated schema. The 2FA impact is a **security/availability** concern: SMS users lose account recovery.

### 2. Batch and external consumers depend on the column

- Nightly cron — `lib/tasks/export_support.rake:17`:
  > `User.where.not(phone: nil).pluck(:id, :email, :phone).each { |row| Csv.append(row) }`
  > Nightly cron at 02:00 UTC, writes to `s3://support-exports/users/`.
  This query references the column by name; it fails on the next run after the migration.
- Partner CRM — `docs/integrations/crm-sync.md:8`:
  > the CRM pulls `/api/v1/users` and uses `phone` as the primary contact field.
  Removing `phone` from the API response (which follows removing it from the slice) is a **contract break** for a downstream integration.

### 3. Blanket "we no longer need this" is false on the facts

The developer said phone login was replaced with email+password. That removes phone as a *login* credential. It does **not** remove phone as (a) a validated profile attribute, (b) an API field, (c) a support-export field, (d) a CRM contact field, or (e) the **only** 2FA recovery channel. Those are distinct uses.

### 4. Unsearched surfaces

`application-usage.md` explicitly states these were **not** searched:
- Mobile clients (separate repository, not present here).
- BI dashboards (Metabase, separate instance).

Given the API already returns `phone`, a mobile client reading it is plausible; a BI dashboard querying `phone` is likewise plausible. Their status is **unknown**, not cleared. This alone prevents a "safe" verdict.

### 5. Schema-side dependency

`db/schema.sql` declares `users_phone_idx` on `phone`. Dropping the column drops the index — harmless in isolation, but relevant if the intent is to keep the column and deprecate use first.

### 6. Deploy-topology makes it worse

From `context.md`:
- Rolling deploy, 2 replicas, zero downtime.
- "The release containing the 'phone removed' code **has not been deployed yet**. `main` still contains the phone code."

So the running code expects `phone`. A migration that drops the column either (a) runs before the code change and breaks running instances, or (b) if merged with code, runs during a rolling window where old instances still issue `SELECT ... phone` / `INSERT ... phone` — both fail. Either ordering is unsafe. Zero-downtime deploy and `DROP COLUMN` are incompatible here.

### 7. Data loss vs. schema rollback — separated

- **Schema rollback is trivial**: re-adding `ALTER TABLE users ADD COLUMN phone varchar(32);` restores the shape.
- **Data rollback is not**: the column values (≈240,000 non-NULL phones per `schema.sql`) are gone the instant the `DROP` commits. Recovery depends on the nightly `pg_dump` — **24h RPO**, and restores are only done "roughly once a quarter" (i.e., the restore path is cold and slow). If the migration is bad, re-adding the empty column does not bring phone numbers back; you'd need a full restore / point-in-time recovery of the primary.

A "we can just roll back the migration" statement would be wrong: it rolls back the *schema*, not the *data*.

---

## What must happen before this merge

1. **Do not merge** as-is. The column is in active use by `main` code.
2. **Deploy the code change first**, and confirm it is fully rolled out on all replicas:
   - Remove `:phone` from the strong params and the `slice` (or keep it as a deprecated read until consumers migrate — see #4).
   - Remove the model validation.
   - Decide and implement the 2FA SMS recovery story *before* removing `phone`; users relying on it must have an alternative or be migrated/notified.
   - Update `lib/tasks/export_support.rake` to stop selecting `phone` (or read from an alternative source).
   - Notify the CRM partner team and agree a deprecation date for the `phone` API field.
3. **Close the unknown surfaces**: check the mobile repo and Metabase for `phone` references; record results in the usage doc.
4. **Prefer a two-step deprecation** rather than an instant `DROP`:
   - Step A: stop reading/writing `phone` in code; keep the column.
   - Step B (a release later): stop the nightly export/API field; confirm no consumers.
   - Step C (later still): `ALTER TABLE users DROP COLUMN phone;` in a transaction, after confirming backups.
5. If dropping goes ahead later, treat the `users_phone_idx` drop and the PII deletion as separate concerns; verify the dump retention/RPO is acceptable for the ~240k phone numbers being destroyed.

---

## Summary table

| Check | Result |
|---|---|
| Column still referenced by application code on `main` | **Yes** — controller, model, 2FA service |
| Column still referenced by batch/external consumers | **Yes** — export cron, CRM integration |
| Downstream clients confirmed clear | **No** — mobile and BI explicitly unsearched |
| Data loss recoverable by schema rollback | **No** — restore depends on 24h RPO dump |
| Compatible with rolling/zero-downtime deploy | **No** — old instances reference the column |
| Developer's "unused" claim substantiated | **No** — contradicted by usage report |

**Verdict: BLOCK.** Merge only after the application, batch, and external consumers are removed/migrated, unknown clients are checked, and the change is sequenced as a deprecation-then-drop across deploys.