# Migration Guard Report — PG-001

## Verdict

**BLOCK — do not merge.**

The migration is a **destructive, irreversible-in-practice** `DROP COLUMN` on an attribute that is actively read, written, exported, and depended upon by an external partner. The developer's premise ("it is unused") is contradicted by the codebase. This is both a **data-loss** problem and a **schema-rollback / zero-downtime** problem, and those are separate issues that each independently justify blocking.

Proposed change under review:

```sql
-- db/migrate/20261115093000_remove_phone_from_users.sql
ALTER TABLE users DROP COLUMN phone;
```

---

## Evidence

All quotes are from the supplied `application-usage.md` and `schema.sql`.

### 1. Active application reads and writes

Every one of these still runs on `main`, which is what gets deployed:

- **Controller permit (write path)** — `app/controllers/users_controller.rb:14`:
  > `permitted = params.require(:user).permit(:email, :phone, :display_name)`

- **API response (read path)** — `app/controllers/users_controller.rb:31`:
  > `render json: @user.slice(:id, :email, :phone, :display_name)`
  > "→ the profile endpoint returns `phone` in the API response body."

- **Model validation** — `app/models/user.rb:9`:
  > `validates :phone, format: { with: /\A\+?[0-9 ]{7,20}\z/ }, allow_blank: true`

- **2FA SMS delivery** — `app/services/two_factor/send_code.rb:22`:
  > `return unless user.phone.present?`
  > `SmsGateway.deliver(to: user.phone, body: code)`
  > "→ phone is the *only* 2FA recovery channel for SMS users."

After the migration, `user.phone` raises `NoMethodError`/`ActiveModel::MissingAttributeError`, the permit silently drops the param, and SMS 2FA recovery breaks for every SMS user.

### 2. Batch and external consumers

- **Nightly export** — `lib/tasks/export_support.rake:17`:
  > `User.where.not(phone: nil).pluck(:id, :email, :phone).each { |row| Csv.append(row) }`
  > "Nightly cron at 02:00 UTC, writes to `s3://support-exports/users/`."

- **Partner CRM (primary contact field)** — `docs/integrations/crm-sync.md:8`:
  > "the CRM pulls `/api/v1/users` and uses `phone` as the primary contact field."

Dropping the column breaks the nightly job (references a non-existent column) and the partner contract.

### 3. Index dependency

- `db/schema.sql`:
  > `CREATE INDEX users_phone_idx ON users (phone);`
  > "dropping the column drops the index."

### 4. Data volume / value at risk

- `db/schema.sql`:
  > "Rows: ~310,000. Approximately 240,000 have a non-NULL phone."
  > "phone is used as a 2FA recovery channel and is exported nightly to the support tooling."

Dropping the column permanently discards ~240,000 PII values that are the sole SMS-2FA recovery path, with no capture step.

### 5. Codebase was only partially searched

`application-usage.md` states under "Not searched":
> "- Mobile clients (separate repository, not present here).
> - BI dashboards (Metabase, separate instance)."

This means the true blast radius is a **lower bound**. Even the known usages already block the change.

---

## Two distinct problems

### A. Data loss (irreversible)

`DROP COLUMN` is not a schema "rollback" — the data is gone. The 24h `pg_dump` gives a 24h RPO window; restores happen "roughly once a quarter," so a problem discovered later may only be recoverable by an out-of-band quarterly restore, not a migration rollback. The claim that it is "PII we no longer need to store" is a policy decision that must be validated and, if accepted, executed as an explicit **capture-then-delete** step — not a bare `DROP`.

### B. Zero-downtime / schema rollback (separate from A)

Rolling deploy with old and new running simultaneously, and the release removing phone code "has **not** been deployed yet." Any `DROP COLUMN` appears first in the schema while old replicas (and the old query code) still run. Even if the release were deployed first, the old/new overlap window still means schema and code are out of sync. Column removal must follow the **expand → migrate → contract** pattern, not precede the code.

---

## Required remediation

Do **not** merge as-is. To proceed, the change must be replaced with a staged plan:

1. **Deploy code first** — remove all `phone` reads/writes from `main`:
   - controller permit and response slice,
   - model validation,
   - 2FA `send_code` (migrate to an alternate channel/strategy),
   - export rake, partner CRM contract (or version the API and confirm a cutover date).
   The unsearched mobile clients and BI dashboards must be audited and cleared.

2. **Stop and confirm the export and CRM** — verify `export_support.rake` and the CRM sync no longer depend on the column *after* the deployed code no longer serves it.

3. **Decide PII retention explicitly** — if the ~240,000 phones must go, capture/delete via a deliberate step (e.g., a separate data anonymization task with sign-off), not an implicit schema drop.

4. **Only then contract** — add a separate, reviewed migration that drops the column (and `users_phone_idx` goes with it), after the new code has been live and stable.

Until every one of those is verifiably done, the migration remains **BLOCKED**.