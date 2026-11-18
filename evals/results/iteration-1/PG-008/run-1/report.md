# Migration Guard Report — PG-008

**Migration:** `db/migrate/20261116104500_rename_phone_to_phone_number.sql`
**Change:** `ALTER TABLE users RENAME COLUMN phone TO phone_number;`
**Engine:** PostgreSQL 15 · **Table:** `users` (~310k rows)

---

## Verdict: ❌ NOT SAFE TO MERGE AS-IS

The DDL is cheap, but it breaks the rolling deploy. Merging this migration will cause errors on live traffic during the deploy window and will break the nightly export CronJob until it is rebuilt.

---

## Lock analysis (the part that *is* fine)

- A `RENAME COLUMN` takes `ACCESS EXCLUSIVE` on `users` but completes almost instantly (metadata-only). There is no rewrite or table scan.
- The consequence is a **brief lock**, and it satisfies the skill's threshold for lock risk. That is not the problem here.

## Deploy-order incompatibility (the actual defect)

`context.md` states:

> "The migration runs **before** the new application pods start."
> "Rolling deploy: for the duration of the deploy, old code (reading `phone`) and new code (reading `phone_number`) run against the same schema."

Deployed references to `phone`, from `application-usage.md`:

> `app/controllers/users_controller.rb:31` — `render json: @user.slice(:id, :email, :phone, :display_name)`
> `app/services/two_factor/send_code.rb:22` — `SmsGateway.deliver(to: user.phone, ...)`
> `app/models/user.rb:9` — `validates :phone, ...`

**Timeline after `ALTER`:**

| Phase | Schema | Running code | Result |
|---|---|---|---|
| Migration runs | `phone` → `phone_number` | old pods still live | **old code queries `phone` → `column "phone" does not exist`** |
| Old + new pods overlap | `phone_number` only | mix | every old pod 500s on `users_controller#show`, 2FA send, validation |
| All new pods live | `phone_number` | new code | OK until export job runs (see below) |

> The rename is only instant in *execution time*; it is not compatible with code that still reads the old name. The rolling deploy guarantees this overlap.

## Background job breakage

`application-usage.md`:

> `lib/tasks/export_support.rake:17` — `User.where.not(phone: nil).pluck(:id, :email, :phone)`
> "This job runs as a Kubernetes CronJob built from the **last released image**, so it keeps using `phone` until a new image is released and the CronJob spec is updated."

So even after the app deploy finishes, the **nightly export** keeps querying `phone` until a separate image release plus CronJob spec update. It will fail every night starting the day after the migration.

## What I confirmed safe

- Index: "`users_phone_idx` is on `phone`. PostgreSQL renames the column and keeps the index attached (the index name is unchanged), so query plans are preserved." ✅ The helper does not need to be rebuilt; only the name is now misleading.
- Views: "No database views reference `phone`." ✅
- No counter indexes or FK dependents flag up.
- Mobile clients: out of scope for this check ("API-contract change outside this skill's scope"), though a renamed JSON key is a hard API break that deserves its own review. Noted for the PR owner.

## Required changes to make this shippable

Use the **expand/contract ("parallel change")** pattern across releases, not a one-shot rename:

**Release 1 (this migration + code change)**
- Do **not** rename. Add a new nullable column instead:
  ```sql
  ALTER TABLE users ADD COLUMN phone_number varchar(32);
  ```
  This is metadata-only and takes a brief `ACCESS EXCLUSIVE` lock.
- Backfill in batches, outside the migration:
  ```sql
  UPDATE users SET phone_number = phone WHERE phone_number IS NULL;  -- batch by id ranges
  ```
- Dual-write from the app: keep writing `phone`, also write `phone_number`; keep reading `phone`. Deploy this code *before* or *with* the additive migration.

**Release 2 (after all old pods and the CronJob image are gone)**
- Switch reads to `phone_number`.
- Update `export_support.rake` to use `phone_number` and release an image; confirm the CronJob spec points at the new image.
- Add a trigger or keep dual-writing to prevent drift during the window.

**Release 3 (cleanup, after stable)**
- Drop `phone`, rename `phone_number` if the exact name matters, and recreate the index:
  ```sql
  ALTER TABLE users DROP COLUMN phone;  -- confirm the old client is gone first
  -- optionally rename phone_number -> phone, but only if the API contract was the reason
  ```
- Rename `users_phone_idx` to match the new column name, so the name is not misleading.

**Prerequisites to prove before merging:**
1. No remaining reads of `phone` in released images (the CronJob is the known straggler).
2. The overlap window is covered by dual-write or a compatibility view.
3. The API JSON-key change (mobile clients) is handled as a versioned/deprecated contract change.

---

## Summary table

| Check | Result |
|---|---|
| Lock duration | ✅ Instant `ACCESS EXCLUSIVE` |
| Index preservation | ✅ Index follows the column |
| Views | ✅ None |
| Old code during deploy | ❌ Old pods query `phone` → error |
| Background CronJob (last image) | ❌ Stays on `phone` for multiple days |
| API contract | ⚠️ JSON key renamed (out of scope, but breaking) |
| **Merge recommendation** | **❌ Block — replace with expand/contract** |