# Migration Guard Report — PG-001

## Migration under review

`db/migrate/20261115093000_remove_phone_from_users.sql`

```sql
-- db/migrate/20261115093000_remove_phone_from_users.sql
ALTER TABLE users DROP COLUMN phone;
```

## Verdict

**BLOCK — do not merge.**

The migration is irreversible in the destructive sense (it deletes live data and the 2FA channel), is unsafe under a rolling deploy, and drops an index a nightly export depends on. The claim "it is unused" is contradicted by the codebase evidence supplied.

---

## Evidence

### 1. Application code on `main` still reads and writes `phone`

- **Strong params** (write path):
  > `app/controllers/users_controller.rb:14` — `permitted = params.require(:user).permit(:email, :phone, :display_name)`
- **API response** (read path — user-visible):
  > `app/controllers/users_controller.rb:31` — `render json: @user.slice(:id, :email, :phone, :display_name)`
  > "the profile endpoint returns `phone` in the API response body."
- **Model validation**:
  > `app/models/user.rb:9` — `validates :phone, format: { with: /\A\+?[0-9 ]{7,20}\z/ }, allow_blank: true`
- **2FA delivery** (the critical one):
  > `app/services/two_factor/send_code.rb:22` — `return unless user.phone.present?` / `SmsGateway.deliver(to: user.phone, body: code)`
  > "phone is the *only* 2FA recovery channel for SMS users."

Dropping the column causes the profile endpoint to raise `ActiveRecord::StatementInvalid`/missing-attribute errors, breaks the validates line at model load/validation time, and silently disables SMS 2FA for ~240k users with a non-NULL phone.

### 2. Batch / external consumers

- **Nightly support export**:
  > `lib/tasks/export_support.rake:17` — `User.where.not(phone: nil).pluck(:id, :email, :phone)...`
  > "Nightly cron at 02:00 UTC, writes to `s3://support-exports/users/`."
- **Partner CRM**:
  > `docs/integrations/crm-sync.md:8` — "the CRM pulls `/api/v1/users` and uses `phone` as the primary contact field."

Both break on or immediately after the deploy.

### 3. Deploy ordering makes it worse, not better

> "Rolling deploy: old and new application versions run simultaneously during the deploy window."
> "The release containing the 'phone removed' code has **not been deployed yet**. `main` still contains the phone code; the migration branch is separate."

The migration branch is separate from a code branch that has not shipped. If the migration is merged and run ahead of (or alongside) the still-current `main`, every replica running current `main` will be pointed at a table missing `phone`. Even if a "phone removed" branch exists, the rolling window guarantees old code and the new schema coexist — old replicas will fail on the profile endpoint, validation, and 2FA.

### 4. Schema-side dependency

> `db/schema.sql` declares `users_phone_idx` on `phone`; dropping the column drops the index.

Confirms the index loss is a side effect, not a stated decision.

### 5. Out of scope but relevant

> Not searched: Mobile clients (separate repo), BI dashboards (Metabase, separate instance).

The CRM is already a confirmed consumer. Mobile clients and BI likely also use `phone`, which strengthens the case against dropping before those are migrated.

---

## What the migration does and does not do

- **Destroys data:** it deletes ~240,000 live phone values. This is a data-loss event, not a schema change you can "roll back." A `pg_dump` (24h RPO) exists, but restores are "performed roughly once a quarter" — that RPO is for disaster recovery, not a convenient undo button for a bad deploy.
- **Rollback:** re-adding the column (`ALTER TABLE users ADD COLUMN phone varchar(32)`) restores *structure*, not *values*. Recreated empty, the 2FA channel comes back blank and the export yields nothing. Filesystem/git rollback of the migration file is therefore **not** a real rollback.
- **Schema-only rollback** is possible; **data rollback** requires a restore, which reverts the whole DB to the dump point and loses all writes since.

---

## Required remediation before any merge

1. **Ship code first, drop later.** Remove `phone` from `users_controller.rb:14`, `:31`, `user.rb:9`, and the 2FA flow (or explicitly retire SMS 2FA and communicate it). Deploy that to 100% of replicas. Only then can the column become a candidate.
2. **Migrate the consumers.** Update `lib/tasks/export_support.rake:17` and coordinate the CRM (`crm-sync.md`) to stop using `phone`. Check mobile + BI before declaring it dead.
3. **Confirm no live data dependency.** Given ~240k non-NULL rows and a 24h RPO with quarterly restores, you need an explicit, verified data-retention decision — "PII we no longer need" should be confirmed against legal/compliance and the export consumer.
4. **If you still want to remove it:** use a staged migration (e.g., rename/`DELETE`-back afterwards) and drop the column in a separate migration *after* the code is fully retired, not in the same deploy.
5. **Re-run this review** on the actual removal migration once steps 1–2 are evidenced.

## Summary

| Check | Result |
|---|---|
| Column still read by app code | ✅ Yes — controller, model, 2FA service |
| Column still exported | ✅ Yes — nightly S3 export |
| External consumer | ✅ Yes — partner CRM |
| Safe under rolling deploy | ❌ No — old and new replicas coexist |
| Reversible (schema) | ⚠️ Structure yes, data no |
| PII/data-loss reviewed | ❌ Not evidenced |

**Verdict: BLOCK.** `ALTER TABLE users DROP COLUMN phone;` is not safe to merge.