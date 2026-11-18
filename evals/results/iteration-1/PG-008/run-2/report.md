# Migration Guard Report — PG-008

**Migration:** `db/migrate/20261116104500_rename_phone_to_phone_number.sql`
```sql
ALTER TABLE users RENAME COLUMN phone TO phone_number;
```

## Verdict: 🚫 UNSAFE — do not merge as-is

The DDL itself is cheap, but the migration is **deploy-order incompatible**. Merging and running it will break the currently deployed application during the rolling deploy, and will also break the nightly CronJob.

---

## Code touched by this migration

`application-usage.md`:

> - `app/controllers/users_controller.rb:31` — `render json: @user.slice(:id, :email, :phone, :display_name)`
> - `app/services/two_factor/send_code.rb:22` — `SmsGateway.deliver(to: user.phone, ...)`
> - `app/models/user.rb:9` — `validates :phone, ...`
> - `lib/tasks/export_support.rake:17` — `User.where.not(phone: nil).pluck(:id, :email, :phone)`

After the rename, every one of these resolves to `column "phone" does not exist`.

## Deploy-order analysis (the actual risk)

`context.md`:

> - Rolling deploy: for the duration of the deploy, old code (reading `phone`) and new code (reading `phone_number`) run against the same schema.
> - The migration runs **before** the new application pods start.
> - A background export job reads `users.phone` (`lib/tasks/export_support.rake`), continuously every night.

Sequence after merge:

1. Migration runs → column is now `phone_number`; `phone` no longer exists.
2. Old pods (3 replicas, still draining) continue serving traffic against `phone` → `PG::UndefinedColumn` on every request hitting `users_controller#show`, `User` validation, and 2FA SMS send. This is a **user-facing outage during the deploy window**, not a theoretical one.
3. New pods come up and use `phone_number` — but step 2 has already failed behind them.
4. The CronJob is built from the **last released image** (`application-usage.md`: *"keeps using `phone` until a new image is released and the CronJob spec is updated"*). It will fail **every night** until a new image ships and the CronJob manifest is updated — a separate, independent breakage.

## What the migration does right

- `application-usage.md`: *"`users_phone_idx` is on `phone`. PostgreSQL renames the column and keeps the index attached (the index name is unchanged), so query plans are preserved."* — correct; no index rebuild.
- *"No database views reference `phone`."* — no view breakage.
- Lock profile: `RENAME COLUMN` takes `ACCESS EXCLUSIVE` but rewrites no data and is O(1) even at ~310k rows. Lock cost is not the problem here.

## Why "the rename is instant" is not the safety argument

`context.md` states the case directly:

> This is the "instant but breaking" case. A rename takes `ACCESS EXCLUSIVE` but completes immediately, so lock analysis alone says "safe". The actual risk is deploy-order incompatibility: after the rename and before the new pods are live, old pods raise `column "phone" does not exist` on every request that touches it.

Instant DDL + old code = outage. Accepting this migration requires proving the old image never references `phone` — which the evidence contradicts.

## Not covered by this report

`application-usage.md`: *"Mobile clients (they call the API, so they follow the API field name; the API renames the JSON key in the new version, which is an API-contract change outside this skill's scope.)"* — flagged as an API-contract change for the API owners, not a schema blocker.

---

## Required changes before merge

Use the expand/contract ("dual-write, then cut over") pattern. One migration alone cannot do this safely for a rolling deploy.

**Phase 1 — expand (merge & deploy now):**
```sql
ALTER TABLE users ADD COLUMN phone_number varchar(32);

-- keep writers in sync with the old column without an app deploy
CREATE FUNCTION users_sync_phone() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  IF NEW.phone_number IS NOT DISTINCT FROM OLD.phone_number
     AND NEW.phone IS DISTINCT FROM OLD.phone THEN
    NEW.phone_number := NEW.phone;
  ELSIF NEW.phone IS NOT DISTINCT FROM OLD.phone
     AND NEW.phone_number IS DISTINCT FROM OLD.phone_number THEN
    NEW.phone := NEW.phone_number;
  END IF;
  RETURN NEW;
END $$;

CREATE TRIGGER users_sync_phone_trg
  BEFORE INSERT OR UPDATE ON users
  FOR EACH ROW EXECUTE FUNCTION users_sync_phone();

CREATE INDEX CONCURRENTLY users_phone_number_idx ON users (phone_number);
```
- New code writes both columns (or relies on the trigger), old code keeps working.
- Backfill `phone_number` where it was never written by the old code (the trigger only fires on updates; run a batched `UPDATE ... WHERE phone_number IS NULL AND phone IS NOT NULL`).

**Phase 2 — cut over (after new image is fully rolled out and the CronJob image has been bumped):**
- Confirm no process still reads `phone` (grep image tags / CronJob manifest).
- Drop the trigger, then drop the old column and old index. Dropping `phone` requires the `users_phone_idx` to go with it.

**Phase 3 — cleanup** (optional) — nothing left referencing `phone`.

The CronJob image/spec update must land **before** the old column is dropped, not after.

## Summary

| Check | Result |
|---|---|
| DDL cost at 310k rows | ✅ O(1), no rewrite |
| Index preservation | ✅ `users_phone_idx` follows the column |
| Database views | ✅ none |
| Old app code during rolling deploy | ❌ old pods will error on every request using `phone` |
| Nightly CronJob (old image) | ❌ fails every night until image bump |
| Backward-compatible / expand-contract | ❌ single destructive rename |

**Verdict: REJECT.** Replace the single rename with the expand/contract sequence above; do not merge until the expand migration is in place and the CronJob image/spec update is scheduled as part of the same change set.