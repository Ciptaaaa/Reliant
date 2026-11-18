# Migration Guard Report — `20261115093000_remove_phone_from_users.sql`

## Verdict

**DO NOT MERGE / DO NOT DEPLOY — BLOCKED**

The developer's premise ("it is unused") is **false**. `phone` is actively read and written across the application, used as the sole SMS 2FA recovery channel, exported nightly, and consumed by an external partner CRM. This migration both (a) breaks the running application and (b) irreversibly destroys ~240,000 PII records that are in active production use.

---

## Evidence

### 1. Application still writes `phone` — code path will break on deploy

`app/controllers/users_controller.rb:14`:
> `permitted = params.require(:user).permit(:email, :phone, :display_name)`

This is a direct write path that is live on `main`. The migration branch is separate, and `context.md` confirms:
> "The release containing the 'phone removed' code has **not been deployed yet**. `main` still contains the phone code; the migration branch is separate."

A migration that runs before/independently of the code change will cause **`ActiveModel::UnknownAttributeError` / `PG::UndefinedColumn`** on any user create/update as soon as the column disappears.

### 2. Application still reads `phone` in API responses

`app/controllers/users_controller.rb:31`:
> `render json: @user.slice(:id, :email, :phone, :display_name)`

`slice` on a missing attribute raises once the column is dropped — the profile endpoint (and `/api/v1/users`, per the CRM contract) will 500.

### 3. Model validation references the column

`app/models/user.rb:9`:
> `validates :phone, format: { with: /\A\+?[0-9 ]{7,20}\z/ }, allow_blank: true`

Any code path invoking `valid?`/`save` on a `User` will error with `undefined method 'phone'`.

### 4. Drop breaks the sole 2FA SMS recovery channel

`app/services/two_factor/send_code.rb:22`:
> `return unless user.phone.present?`
> `SmsGateway.deliver(to: user.phone, body: code)`

`application-usage.md` states explicitly:
> "phone is the *only* 2FA recovery channel for SMS users."

Dropping the column silently **locks out every SMS-based 2FA user** from recovery — a security/availability incident, not a cleanup.

### 5. Batch consumer crashes nightly

`lib/tasks/export_support.rake:17`:
> `User.where.not(phone: nil).pluck(:id, :email, :phone).each { |row| Csv.append(row) }`

Nightly cron at 02:00 UTC to `s3://support-exports/users/`. This job will fail on the first run post-migration, and support tooling loses its export feed.

### 6. External partner contract

`docs/integrations/crm-sync.md:8`:
> "the CRM pulls `/api/v1/users` and uses `phone` as the primary contact field."

This is an **out-of-repo consumer**. Dropping the field is a breaking API change with no deprecation period visible here.

### 7. Schema dependency

`db/schema.sql`:
> `CREATE INDEX users_phone_idx ON users (phone);`

Dropping the column implicitly drops the index — acceptable *only* if nothing queries by phone, which is not established.

### 8. Unsearched consumers of unknown size

From `application-usage.md`:
> "Not searched: Mobile clients (separate repository, not present here). BI dashboards (Metabase, separate instance)."

Mobile clients and BI dashboards are **unverified**. Mobile clients very plausibly display/edit `phone`; Metabase dashboards may `SELECT phone`. This alone precludes a merge decision from the evidence presented.

### 9. Data loss is irreversible under the stated RPO

- ~240,000 non-NULL `phone` values destroyed (PII in active use, not a dead field).
- Restore point: "Nightly `pg_dump` to object storage, 24h RPO. Restores are performed roughly once a quarter."
- A restore is a **last resort with 24h data loss**, not a rollback mechanism. Re-adding the column after drop returns an all-NULL column — the data is gone.

`context.md` frames the task as separating **data loss** from **schema rollback**. Here:
- **Schema rollback**: trivial (`ALTER TABLE users ADD COLUMN phone varchar(32)`).
- **Data rollback**: impossible without a full restore. The migration destroys live PII that the business still uses.

---

## Recommended path (not a blocker resolution — a redesign)

Do **not** merge this migration. Instead, run a proper drop-column sequence across multiple releases:

1. **Release N (code)**: Stop reading/writing `phone`; migrate SMS 2FA users to an alternative recovery channel; remove `phone` from API serializers; instruct CRM to switch to `email`; update `export_support.rake`. Deploy and confirm no accesses for a full observation window.
2. **Release N+1 (schema, reversible)**: Rename instead of drop: `ALTER TABLE users RENAME COLUMN phone TO phone_deprecated;` — instant, reversible, keeps data.
3. **Observation window** (weeks): Watch DB slow logs, API 4xx/5xx, partner error reports, Metabase dashboards, mobile crash reports.
4. **Release N+2 (destructive, after explicit sign-off)**: `ALTER TABLE users DROP COLUMN phone_deprecated;` and drop `users_phone_idx`.

Only step 4 is irreversible, and only after the evidence gap (mobile/BI) is closed.

---

## Summary table

| Concern | Status |
|---|---|
| Read path broken | ✅ Confirmed (`users_controller.rb:31`) |
| Write path broken | ✅ Confirmed (`users_controller.rb:14`, `user.rb:9`) |
| 2FA recovery broken | ✅ Confirmed (`send_code.rb:22`) |
| Nightly batch broken | ✅ Confirmed (`export_support.rake:17`) |
| External partner broken | ✅ Confirmed (`crm-sync.md:8`) |
| Index dropped | ✅ Confirmed (`schema.sql`) |
| Unverified consumers | ⚠️ Mobile clients, Metabase |
| Irreversible data loss | ⚠️ ~240k phone records; 24h RPO only |
| Schema rollback possible | ✅ Trivial |
| Data rollback possible | ❌ Only via quarterly restore |

**Verdict: BLOCKED. Unsafe to merge or deploy in its current form.**