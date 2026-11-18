# Migration Guard Report — PG-008

## Migration under review

```sql
-- db/migrate/20261116104500_rename_phone_to_phone_number.sql
ALTER TABLE users RENAME COLUMN phone TO phone_number;
```

## Verdict: **BLOCK — not safe to merge as-is**

The rename is DDL-instant, but it is **not deploy-safe**. It breaks the currently deployed application and the still-running background job the moment it is applied, and it stays broken until *all* old code is gone. This must be split into an expand/contract sequence.

---

## Evidence

### 1. Old code reads `phone` — and old code runs during the deploy

From `application-usage.md` (current, deployed code):

> - `app/controllers/users_controller.rb:31`
>   ```ruby
>   render json: @user.slice(:id, :email, :phone, :display_name)
>   ```
> - `app/services/two_factor/send_code.rb:22` — `SmsGateway.deliver(to: user.phone, ...)`
> - `app/models/user.rb:9` — `validates :phone, ...`

From `context.md` (deployment facts):

> - Rolling deploy: for the duration of the deploy, old code (reading `phone`) and new code (reading
>   `phone_number`) run against the same schema.
> - The migration runs **before** the new application pods start.

Sequencing after merge:

1. Migration runs → column is now `phone_number`.
2. Migration finishes, new pods begin to start, old pods are still serving traffic (3 replicas, rolling).
3. **Every old pod still running** raises `column "phone" does not exist` on:
   - the user serializer (`GET /users`, line 31),
   - the 2FA SMS send path (line 22),
   - **and any `User` load**, because `validates :phone` (line 9) causes Rails to select/assign `phone` on save, and model attribute validation against an absent column surfaces errors on writes.

This is not a narrow read-path failure: the model-level validation makes the breakage hit essentially every write to `users`, not just the two lines cited. If the DB migration commits before the new replicas exist (Rails DB-migrate mode is explicitly set in `context.md`), the outage window is bounded by pod start time, so this is a real gap; but any failing old pod is broken until rescheduled, and the 2FA delivery path is user-visible.

### 2. Background job keeps reading `phone` long after the deploy finishes

From `context.md`:

> A background export job reads `users.phone` (`lib/tasks/export_support.rake`), continuously every
> night.

From `application-usage.md`:

> - `lib/tasks/export_support.rake:17` — `User.where.not(phone: nil).pluck(:id, :email, :phone)`.
>   This job runs as a Kubernetes CronJob built from the **last released image**, so it keeps using
>   `phone` until a new image is released and the CronJob spec is updated.

So even after the application rolls over, the CronJob image is still old. This job runs *nightly*, which means the breakage can occur up to 24 hours after a "finished" migration. The migration is unsafe until a *separate* image-release-and-CronJob-spec-update step is complete — a dependency absent from `migration.sql` and from context. This is the second deploy-order contradiction and is the one most likely to be missed.

### 3. The stated safety argument is a lock argument, not a compatibility argument

`context.md`:

> The rename is instant in PostgreSQL, so it should be safe, right?

PostgreSQL replaces the column's `pg_attribute` entry atomically under `ACCESS EXCLUSIVE`. The DDL cost is negligible; the *compatibility* cost is not. The migration trades a long lock for an instant one that immediately orphans in-flight code. The index `users_phone_idx` and the absent views give no protection against this; they simply tell us the index and view layers are safe to rename while the code layer is not.

### 4. Every premise the migration relied on for safety is contradicted by the deployment facts

| Assumption | Reality (quoted) |
|---|---|
| Rename is instant | true for DDL only |
| Only the API contract changes | model validator, SMS path, CronJob also break |
| CronJob tracks the app | "keeps using `phone` until a new image is released and the CronJob spec is updated" |

---

## Required changes

This is a classic expand/contract. Do **not** rename in place. Apply across three separate migrations/releases:

**Step 1 — expand**
```sql
-- add the new column; do NOT drop or rename
ALTER TABLE users ADD COLUMN phone_number varchar(32);
CREATE INDEX CONCURRENTLY users_phone_number_idx ON users (phone_number);
```
Then backfill `phone_number = phone` in batches, and make the old column nullable/no-writer while keeping it. Deploy code that **writes both** and **reads `phone_number` with a fallback to `phone`**. At this point both old and new code can coexist.

**Step 2 — release a new CronJob image + update the CronJob spec** to read `phone_number`. Do not proceed to Step 3 until that is live and at least one nightly run has succeeded against the dual-write schema.

**Step 3 — contract** (only after no code reads or writes `phone` and logs confirm the legacy path is dead):
```sql
ALTER TABLE users DROP COLUMN phone;
DROP INDEX CONCURRENTLY IF EXISTS users_phone_idx;
```
The `users_phone_idx` index is dropped only in this step; note that `CREATE INDEX CONCURRENTLY` cannot run inside the migration transaction, so this should be a `disable_ddl_transaction!` migration or a separate ops step. (The `users_phone_idx`-renamed-index point from `application-usage.md` is now moot because we are not renaming.)

The API-contract change (renaming the JSON key `phone` → `phone_number`) is explicitly "outside this skill's scope" per `application-usage.md`; it needs to be coordinated with mobile clients separately, and is *not* a reason to merge the in-place rename.

---

## Blocking issues summary

1. **Old application pods + migration-before-new-pods** → guaranteed `column "phone" does not exist` window on controller, SMS, and model validation paths.
2. **CronJob from last released image** keeps reading `phone` for at least one nightly run after the new app is live.
3. In-place rename is irreversible-by-design and has no rollback path once old code is gone.

Merge this only as part of the expand/contract sequence above. As written, **block**.