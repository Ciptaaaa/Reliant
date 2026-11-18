# Migration Guard Report — PG-001

## Verdict: **REJECT — BLOCK MERGE**

The migration `ALTER TABLE users DROP COLUMN phone;` is **not safe**. The developer's premise ("it is unused") is factually wrong: `phone` is actively read, written, validated, exported, and serves as the *only* SMS 2FA recovery channel. This is a destructive, irreversible data-loss operation against a live traffic path.

---

## 1. Evidence that `phone` is still in use

The developer asserts the column is unused. The application-usage trace contradicts this in **every category** (direct reads/writes, batch consumers, external integrations):

**Direct reads/writes (still on `main`):**
- `app/controllers/users_controller.rb:14` — **writes**: `permit(:email, :phone, :display_name)`. New/changed phones are still persisted.
- `app/controllers/users_controller.rb:31` — **reads**: `render json: @user.slice(:id, :email, :phone, :display_name)`. The profile endpoint *returns* `phone` in the API body.
- `app/models/user.rb:9` — `validates :phone, format: {...}`. The model still references the attribute; dropping the column will raise on validation of any write touching it.
- `app/services/two_factor/send_code.rb:22`:
  > `return unless user.phone.present?`
  > `SmsGateway.deliver(to: user.phone, body: code)`
  > "→ phone is the *only* 2FA recovery channel for SMS users."

  This is the most severe finding. After the drop, `user.phone` raises `NoMethodError`/`ActiveModel::MissingAttributeError` for SMS users, **breaking account recovery / 2FA login** — a security-adjacent availability failure, not just a cosmetic bug.

**Batch / external consumers:**
- `lib/tasks/export_support.rake:17` — nightly cron (02:00 UTC): `User.where.not(phone: nil).pluck(:id, :email, :phone)`. The query references `phone` → will fail outright with `UndefinedColumn`.
- `docs/integrations/crm-sync.md:8` — "the CRM pulls `/api/v1/users` and uses `phone` as the primary contact field." An external partner depends on the API response field.

**DB-side:**
- `db/schema.sql`: `users_phone_idx` on `phone` is silently dropped with the column.

**Not searched (unknown blast radius):**
- Mobile clients (separate repo) — likely consume the same `/api/v1/users` payload.
- BI dashboards (Metabase) — may query `phone`.

So the correct characterization is: **~240,000 non-NULL rows, live read/write path, a working 2FA recovery channel, a nightly export, and at least one documented partner integration all depend on this column.**

---

## 2. Deployment-window hazard (rolling deploy)

Context states: "Rolling deploy … old and new application versions run simultaneously," and "The release containing the 'phone removed' code has **not** been deployed yet. `main` still contains the phone code."

This means even a *correct* two-phase deploy cannot start with the column drop. During the deploy window, old replicas (running `main`) will execute `user.phone.present?`, `.slice(:phone)`, `permit(:phone)`, and the nightly export against a table where the column no longer exists → immediate errors. The migration cannot land before the code that stops using `phone`.

---

## 3. Data loss vs. schema rollback (separate concerns)

These are distinct risks and both are unaddressed:

- **Data loss is permanent.** After `DROP COLUMN`, the ~240,000 phone values are gone. A `schema.rb`-only rollback recreates an *empty* column; it does **not** restore rows. The only recoverable copy is the nightly `pg_dump` — and context says "Restores are performed roughly once a quarter" with **24h RPO**. Any phone updates in the last 24h before detection are unrecoverable. If this migration is merged and auto-run, recovering requires a full restore that rolls back *all* other changes since the dump.
- **Schema rollback is easy but misleading.** Rails' `change`-less `DROP COLUMN` here is not reversible in practice; even with `reversible`/`up`/`down` blocks, "down" gives you an empty column, not the data.

---

## 4. Missing safeguards

- No backup / snapshot snapshotting step scoped to `users` (only the generic nightly dump).
- No expand/contract plan; the drop is a single destructive step.
- No confirmation that `main`'s phone code has first been removed and deployed.
- No search completed for mobile clients and BI (explicitly "Not searched").

---

## Required remediation before any merge

1. **Do not merge as-is.** First deploy code to `main` that removes all `phone` reads/writes (controller permit/slice, model validation, 2FA service, export task) and migrate SMS 2FA users to an alternative recovery channel. Only then may schema changes proceed.
2. **Warn the CRM partner** and update `docs/integrations/crm-sync.md`; confirm `/api/v1/users` contract change with the integration owner.
3. **Search mobile clients and BI** for `phone` before proceeding (currently unsearched).
4. **Use expand/contract:**
   - Phase 1: deploy code that no longer references `phone`; verify no errors across the rolling window and daily export.
   - Phase 2: take a targeted backup / snapshot of `users` and retain beyond one quarterly-restore cycle.
   - Phase 3 (weeks later, after confirmation): `ALTER TABLE users DROP COLUMN phone;` (or `DROP INDEX users_phone_idx` first if keeping the column).
5. If dropping for PII reasons is the true goal, consider **nulling/anonymizing** the column instead of dropping it — it satisfies minimization without breaking the 2FA recovery path or partner sync.

**Bottom line:** The statement "it is unused" is contradicted by the code and integration docs; this migration would break 2FA recovery, a nightly export, an API response contract, and a partner sync, and it destroys ~240k phone records with only a 24h-RPO safety net. **Reject.**