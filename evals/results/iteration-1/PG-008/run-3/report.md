# Migration Guard Report — PG-008

## Migration under review

`db/migrate/20261116104500_rename_phone_to_phone_number.sql`

```sql
ALTER TABLE users RENAME COLUMN phone TO phone_number;
```

## Verdict: **BLOCK — not safe to merge as-is**

The DDL itself is fast, but it is **backward-incompatible with the running deployed code** during the rolling deploy. It will break old pods and the nightly export job.

---

## Evidence

**1. Deployed code reads `phone`.**
From `application-usage.md`:
> `app/controllers/users_controller.rb:31` — `render json: @user.slice(:id, :email, :phone, :display_name)`
> `app/services/two_factor/send_code.rb:22` — `SmsGateway.deliver(to: user.phone, ...)`
> `app/models/user.rb:9` — `validates :phone, ...`

These run on the 3 old replicas until they are replaced.

**2. Deploy ordering guarantees an incompatibility window.**
From `context.md`:
> "The migration runs **before** the new application pods start."
> "Rolling deploy: for the duration of the deploy, old code (reading `phone`) and new code (reading `phone_number`) run against the same schema."
> "This is the 'instant but breaking' case. … after the rename and before the new pods are live, old pods raise `column \"phone\" does not exist` on every request that touches it."

**3. A background job also depends on `phone` and is not controlled by this deploy.**
From `application-usage.md`:
> `lib/tasks/export_support.rake:17` — `User.where.not(phone: nil).pluck(:id, :email, :phone)`.
> "This job runs as a Kubernetes CronJob built from the **last released image**, so it keeps using `phone` until a new image is released and the CronJob spec is updated."

This dependency outlives the rolling deploy and will fail on the first night after the migration.

**4. Confirmed DB-side facts (these are fine, not the problem).**
From `application-usage.md`:
> "`users_phone_idx` is on `phone`. PostgreSQL renames the column and keeps the index attached (the index name is unchanged), so query plans are preserved."
> "No database views reference `phone`."

So index behavior is preserved; the failure is purely a code/schema contract mismatch across deploy windows.

## Why the lock analysis is misleading here

`ALTER TABLE ... RENAME COLUMN` is a metadata-only change requiring `ACCESS EXCLUSIVE` and completing almost immediately. Lock duration is fine. **Compatibility over time** is not: the schema stops being readable by the code that is still serving traffic.

---

## Required changes before merge

Use an **expand/contract** sequence so both names are usable across the deploy:

**Step 1 (this migration) — expand, non-breaking:**
```sql
ALTER TABLE users ADD COLUMN phone_number varchar(32);
```
Backfill in batches from `phone` (or via trigger/dual-write), and add a read/write compatibility layer:
- New code reads/writes `phone_number`, and continues writing `phone` (or a trigger keeps `phone` in sync).
- Old code keeps working against `phone`.
- The CronJob's `pluck(:id, :email, :phone)` keeps working.

**Step 2 — after all old pods and the CronJob image are replaced and the export task is updated:**
```sql
-- once nothing references `phone`:
CREATE INDEX CONCURRENTLY users_phone_number_idx ON users (phone_number);
DROP INDEX CONCURRENTLY IF EXISTS users_phone_idx;
ALTER TABLE users DROP COLUMN phone;
```

**Step 3 — rename or alias if you still need `phone_number` to be the canonical name** only after Step 2 is fully rolled out. (Adding `phone_number` up front, as above, avoids the rename entirely.)

Also coordinate separately:
- The API JSON-key change (`:phone` → `:phone_number`) is an API-contract change; `application-usage.md` notes it is out of scope for this skill but it must be versioned for mobile clients.

## Summary

- **Lock cost:** negligible (metadata only).
- **Deploy compatibility:** **broken** — deployed Rails controllers, model validation, 2FA SMS and the nightly Kubernetes export CronJob all reference `phone`.
- **Action:** **Reject.** Replace the single `RENAME` with an expand/contract migration and update/deploy the CronJob before dropping `phone`.
