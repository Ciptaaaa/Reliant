# Migration Guard Report — PG-008

**Migration:** `db/migrate/20261116104500_rename_phone_to_phone_number.sql`
**Statement:** `ALTER TABLE users RENAME COLUMN phone TO phone_number;`
**Engine:** PostgreSQL 15 · **Table:** `users` (~310k rows)

---

## 1. DDL cost / lock analysis

- **Lock:** `ALTER TABLE ... RENAME COLUMN` takes `ACCESS EXCLUSIVE` on `users`.
- **Duration:** Metadata-only; completes effectively instantly. No table rewrite, no data copy. Lock hold time is negligible even at 310k rows.
- **Index handling:** `users_phone_idx` is attached to the column and is renamed with it (index name unchanged). Query plans preserved.
- **Views:** "No database views reference `phone`."

On pure lock/blast-radius terms this DDL is cheap. **This is not where the risk is.**

---

## 2. Deploy-order / compatibility analysis

The deployment facts state the decisive constraints:

> "Rolling deploy: for the duration of the deploy, old code (reading `phone`) and new code (reading `phone_number`) run against the same schema."
>
> "The migration runs **before** the new application pods start."

Therefore the timeline is:

1. Migration runs → column is now `phone_number`. Old pods are still serving.
2. New pods roll out gradually over the deploy window.
3. During (1)→(2) and the entire canary/ramp, **old code executes against a schema that no longer has `phone`.**

Every deployed reference to `phone` breaks for that window:

- `users_controller.rb:31` — `render json: @user.slice(:id, :email, :phone, :display_name)` → **every user-show request raises** `column "phone" does not exist`.
- `two_factor/send_code.rb:22` — `SmsGateway.deliver(to: user.phone, ...)` → **2FA delivery fails** for users mid-login.
- `user.rb:9` — `validates :phone, ...` → validation/attribute handling on writes.
- `lib/tasks/export_support.rake:17` — `User.where.not(phone: nil).pluck(:id, :email, :phone)` → the nightly export CronJob. Per `application-usage.md`: "This job runs as a Kubernetes CronJob built from the **last released image**, so it keeps using `phone` until a new image is released and the CronJob spec is updated." This is a **persistent** breakage — not bounded by the rolling deploy. The next nightly run fails `column "phone" does not exist` until a new CronJob image ships.

The developer's reasoning — "The rename is instant in PostgreSQL, so it should be safe, right?" — answers the wrong question. The DDL is instant; the **incompatibility window is the entire rolling deploy plus the CronJob's image-recycle lag**. A fast breaking change is still a breaking change.

The **access pattern** compounds this: the controller serializes `phone` on `show`, so this isn't a rarely-hit code path — it is the primary read path for the user resource, and on a user-facing endpoint.

---

## 3. Gap: the new code is not the only reader to worry about

The missing piece the case wants surfaced: a **schema rename is a three-party change** — DB, running app versions, and any out-of-band consumers (here, the CronJob). The application-usage doc explicitly calls out the CronJob as released-image-pinned, and instructs that it "keeps using `phone` until a new image is released and the CronJob spec is updated." That means the rename **cannot** be shipped atomically with the code even if the rolling deploy were instantaneous.

Additionally, `application-usage.md` notes mobile clients follow the API field name; the rename changes the JSON key `phone → phone_number`, which is an API-contract change (out of scope per the doc, but a reason the rename should not be deployed as a hard cutover regardless).

---

## 4. Verdict

**⛔ BLOCK — do not merge as-is.**

The migration is **not deploy-safe** under the stated rolling-deploy facts. It creates a window (the full rolling deploy) in which old pods raise `column "phone" does not exist` on the user show endpoint, 2FA SMS delivery, and user validation — plus a **persistent** failure of the nightly export CronJob until a new image is released and its spec updated. Lock analysis alone ("instant") masks this; the actual risk is deploy-order incompatibility, exactly as the case warns.

---

## 5. Safe path (expand → migrate → contract)

Do this as **three separate releases**, not one migration + one code branch:

**Release A — add backward-compatible column:**
```sql
ALTER TABLE users ADD COLUMN phone_number varchar(32);
```
No `NOT NULL`, no default (avoids rewrite on PG 15 for plain `ADD COLUMN ... NULL`). Optionally a generated/trigger-backed sync or a backfill job later — but the next step needs dual-write in code, not SQL.

**Release B — deploy code that:**
- writes both `phone` and `phone_number` (or reads with `COALESCE`, writes both), 
- reads `phone_number` with fallback to `phone` until backfill completes,
- emits both JSON keys temporarily (or a versioned API) to protect mobile clients,
- ships the **new CronJob image** so the export uses `phone_number`.
Backfill `phone_number` from `phone` in batches; verify zero drift.

**Release C — contract:**
```sql
ALTER TABLE users DROP COLUMN phone;
```
Only after every consumer reads `phone_number`: old pods drained, CronJob image updated, backfill verified, mobile clients migrated to the new key.

This respects the rolling deploy ("old code and new code run against the same schema") by ensuring the schema is never ahead of the code in a breaking way, and it covers the CronJob — the consumer the original migration forgot.

---

### Evidence relied on
- `migration.sql`: `ALTER TABLE users RENAME COLUMN phone TO phone_number;`
- `context.md`: "Rolling deploy... old code (reading `phone`) and new code (reading `phone_number`) run against the same schema"; "The migration runs **before** the new application pods start."
- `application-usage.md`: `users_controller.rb:31` `slice(..., :phone, ...)`; `send_code.rb:22` `user.phone`; `export_support.rake:17` "runs as a Kubernetes CronJob built from the **last released image**, so it keeps using `phone` until a new image is released and the CronJob spec is updated."
- `application-usage.md`: "PostgreSQL renames the column and keeps the index attached (the index name is unchanged), so query plans are preserved" and "No database views reference `phone`." — these confirm the DDL is cheap but do **not** mitigate the deploy-order breakage.