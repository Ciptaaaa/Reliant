## Migration Guard Report — `db/migrate/20261116104500_rename_phone_to_phone_number.sql`

**Verdict: BLOCK — not safe to merge as written.**

The DDL itself is cheap, but it is a breaking rename under a rolling deploy. The migration runs before the new pods are live, so for the whole deploy window old code and new code share one schema — and only one of them can be satisfied.

---

### 1. What the migration does

```sql
ALTER TABLE users RENAME COLUMN phone TO phone_number;
```

This takes `ACCESS EXCLUSIVE` but completes instantly. Lock cost is not the problem.

---

### 2. Evidence against safety

**Old code reads `phone`, new code reads `phone_number` — simultaneously.**

From `application-usage.md`, the deployed version references `phone` in three places:

- `app/controllers/users_controller.rb:31` — `render json: @user.slice(:id, :email, :phone, :display_name)`
- `app/services/two_factor/send_code.rb:22` — `SmsGateway.deliver(to: user.phone, ...)`
- `app/models/user.rb:9` — `validates :phone, ...`

The context confirms the danger explicitly:

> "Rolling deploy: for the duration of the deploy, old code (reading `phone`) and new code (reading `phone_number`) run against the same schema."
> "The migration runs **before** the new application pods start."

So after the rename and before the new pods are live, every old pod that touches `phone` raises `column "phone" does not exist`. That includes 2FA SMS sends and user rendering — not a silent failure. With 3 replicas on a rolling deploy, this is a guaranteed error window, not a theoretical one.

**The background job outlives the deploy.**

> `lib/tasks/export_support.rake:17` — `User.where.not(phone: nil).pluck(:id, :email, :phone)`
> "This job runs as a Kubernetes CronJob built from the **last released image**, so it keeps using `phone` until a new image is released and the CronJob spec is updated."

Even after the app deploy completes, the export continues to query `phone`. That job will break until a *separate* image build and CronJob spec update land. The rename breaks it at migration time, and nothing in this migration fixes that dependency.

**Rollback is not free either.** Rolling back the code does not roll back the schema automatically; a reverse rename while new pods are live recreates the same inversion.

---

### 3. What the evidence does *not* support

The claim that the rename is "instant, so safe" is correct on lock cost and wrong on compatibility. `context.md` states the case's purpose directly:

> "A rename takes `ACCESS EXCLUSIVE` but completes immediately, so lock analysis alone says 'safe'. The actual risk is deploy-order incompatibility."

On indexes: `users_phone_idx` stays attached under the new name (the index *name* is unchanged). Query plans are preserved — that part is fine, and it is not why this is blocked.

---

### 4. Recommended path (expand / migrate / contract)

Do not merge the bare rename. Split it across releases:

**Release 1 — expand**
- Migration: `ALTER TABLE users ADD COLUMN phone_number varchar(32);` (nullable, instant)
- Backfill (batched, no long lock): `UPDATE users SET phone_number = phone WHERE phone_number IS NULL;`
- Add a trigger or keep both columns in sync in the app so writes to `phone` also populate `phone_number` (and vice versa during transition).
- Deploy app code that reads/writes **both** columns with fallback (read `phone_number`, fall back to `phone`).
- Update `export_support.rake` to select `phone_number`, ship it in the same release, update the CronJob image.

**Release 2 — switch**
- Once all pods and the CronJob are on the new code, and the backfill is verified (`SELECT count(*) FROM users WHERE phone IS NOT NULL AND phone_number IS NULL` = 0), flip all code paths to `phone_number` only.

**Release 3 — contract**
- After the switch is fully deployed and stable: `ALTER TABLE users DROP COLUMN phone;` — and recreate the index on `phone_number` if you want the name to match, inside a `CONCURRENTLY` create/drop.

Alternative if a hard cutover is acceptable: stop writes, take a short maintenance window, run the rename and deploy together. Given 3 replicas and a continuous CronJob, this needs an explicit operator decision rather than being assumed.

---

### 5. Checklist findings

| Check | Result |
|---|---|
| Long lock / table rewrite | PASS (instant rename, no rewrite) |
| Rolling-deploy compatibility (old + new code) | **FAIL** — old code queries `phone` |
| Background jobs / off-deploy consumers | **FAIL** — CronJob runs last released image using `phone` |
| Index / query plan regression | PASS — `users_phone_idx` follows the rename |
| Downstream views | PASS — none reference `phone` |
| Rollback path | FAIL without coordination |
| API contract | Outside scope (noted in usage doc) |

**Required before merge:** restructure this as the expand/migrate/contract sequence above, or get an explicit signed-off maintenance window. The single `ALTER TABLE ... RENAME` is the wrong shape for this deploy.