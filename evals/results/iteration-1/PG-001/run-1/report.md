## Migration Guard Report — PG-001

**Migration:** `db/migrate/20261115093000_remove_phone_from_users.sql`
```sql
ALTER TABLE users DROP COLUMN phone;
```

**Verdict: 🔴 DO NOT MERGE.** The premise is false — `phone` is actively read and written by code on `main`, has downstream consumers, and is a live 2FA channel. This is irreversibly destructive.

---

### Critical blockers

1. **Column is live, not "unused."** The developer's claim contradicts the code:

   > `app/controllers/users_controller.rb:14` — `...permit(:email, :phone, :display_name)`
   > `app/controllers/users_controller.rb:31` — `render json: @user.slice(:id, :email, :phone, :display_name)`
   > `app/models/user.rb:9` — `validates :phone, format: ...`
   > `app/services/two_factor/send_code.rb:22` — `SmsGateway.deliver(to: user.phone, ...)`

   The moment the migration runs, these raise `ActiveRecord::StatementInvalid` / `NoMethodError`. The profile endpoint (`:31`) and 2FA path (`:22`) are user-facing.

2. **2FA lockout hazard.** `send_code.rb:22` — "phone is the *only* 2FA recovery channel for SMS users." Dropping the column permanently destroys the recovery path for ~240,000 SMS users *in one statement*, with no backfill window.

3. **Rolling deploy ordering.** Your deploy facts state the "phone removed" code is **not yet deployed** and `main` still contains the phone code. A DROP column migration must be the *last* step — after code that writes/reads the column is fully gone from **all** replicas. As written, the migration would run while old replicas (still reading `phone`) are serving traffic, causing immediate errors across the rolling window.

4. **Nightly export breaks silently.** `lib/tasks/export_support.rake:17` — `User.where.not(phone: nil).pluck(:id, :email, :phone)`. The 02:00 UTC cron will raise on the next run after merge. Support tooling loses this field with no data path.

5. **External consumers unknown / un-gated.** `docs/integrations/crm-sync.md:8` — CRM "uses `phone` as the primary contact field." Also **not searched**: mobile clients and BI dashboards. Dropping the column breaks these with zero coordination. A `DROP COLUMN` API contract change requires partner sign-off.

6. **Data-loss ≠ schema rollback.** This deserves its own line because the developer framed it as a safety question. `DROP COLUMN` deletes data immediately and cannot be reversed by re-creating the column — a rollback restores the *schema*, not the values. The only recovery path is the nightly `pg_dump` with **24h RPO**, and "restores are performed roughly once a quarter" (context.md). For 240k PII rows, that's an unacceptable recovery window to depend on.

7. **Index dropped with the column.** `db/schema.sql` — `users_phone_idx` on `phone`. No views reference it, but any query relying on that index (current or planned) loses its plan.

---

### Required remediation before any merge

Sequence this as a proper expand/contract migration, not a one-liner:

- **Step 1 (code out first):** Remove all reads/writes from `main` — controller permit/slice, model validation, 2FA, rake export — and coordinate CRM/mobile/BI consumers off `phone`. Deploy and confirm it's gone from all replicas.
- **Step 2 (data):** Decide the retention answer. If PII removal is the goal, use `UPDATE users SET phone = NULL` (or nullify + audit log) as a separate, reversible step — this preserves the column while satisfying the privacy goal.
- **Step 3 (contract, much later):** After a full deploy cycle with no reads, ship the `DROP COLUMN` as its own migration. Even then:
  - Take a verified backup immediately prior, not just the 24h snapshot.
  - Run the migration off the deploy path (after the rolling window closes).
- **Step 4:** Replace the 2FA dependency — either migrate SMS users to a new recovery method or confirm all are migrated *before* nulling.

---

### Summary

| Check | Result |
|---|---|
| Column referenced by code | ❌ Yes (controller, model, 2FA) |
| Downstream consumers | ❌ Export cron, CRM, unsearched mobile/BI |
| Data loss | ❌ 240k rows, irreversible |
| Rollback safety | ❌ Schema-only restore; RPO 24h |
| Deploy ordering | ❌ Migration would run against live old replicas |
| Index/views | ⚠️ Index dropped; no views |

**Verdict: DO NOT MERGE.** This is a schema-level change with data-loss and lockout consequences that contradicts its own justification. It must go through the expand/contract sequence above with consumer sign-off.