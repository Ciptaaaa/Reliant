# Migration Guard Report

## Scope
- **Migration**: `db/migrate/20261116104500_rename_phone_to_phone_number.sql`
- **Engine**: PostgreSQL 15
- **Inspected**: `migration.sql`, `schema.sql`, `application-usage.md`, `context.md`
- **Not inspected**: mobile clients (they follow the API field name; the JSON key change is an API-contract concern outside this skill)
- **Unknowns**: none material; the deploy order is stated explicitly in `context.md`

## Verdict
**CONDITIONAL** — the rename is instant, but for the duration of the rolling deploy the old pods still read `phone`, which no longer exists.

## Findings

### F1 — HIGH — rename
- **Statement**: `ALTER TABLE users RENAME COLUMN phone TO phone_number` in `migration.sql:2` — renames the column immediately, and the migration runs before the new pods start
- **Evidence**:
  - `application-usage.md:5` — `app/controllers/users_controller.rb:31` renders `slice(:email, :phone, :display_name)` in the deployed version
  - `application-usage.md:10` — `app/services/two_factor/send_code.rb:22` delivers the SMS code to `user.phone`
  - `application-usage.md:27` — `lib/tasks/export_support.rake:17` plucks `phone` from the last released image, which runs until the CronJob spec is updated
  - `context.md:17` — the migration runs before the new pods start, and old code keeps running during the rolling deploy
- **Impact**: from the moment the rename commits, every old pod raises `column "phone" does not exist` on the profile endpoint, on 2FA, and in the nightly export, until new pods are live and the CronJob image is updated
- **Mitigation**: use expand-and-contract instead of a direct rename — add `phone_number`, deploy dual write/read, backfill, switch reads, then drop `phone` in a later migration; a direct rename is safe only with an explicitly accepted stop-the-world window
- **Verification**: `grep -rn "phone\b" app/ lib/` in the deployed image returns only `phone_number`, and the CronJob spec is updated before the rename runs
- **Approval**: schedule
- **Reversible**: yes

### F2 — LOW — lock-and-rewrite
- **Statement**: the rename takes an `ACCESS EXCLUSIVE` lock on `users` but completes immediately and keeps `users_phone_idx` attached
- **Evidence**:
  - `migration.sql:2` — `RENAME COLUMN`, a catalog-only change
  - `application-usage.md:33` — "PostgreSQL renames the column and keeps the index attached (the index name is unchanged)"
  - `schema.sql:12` — `CREATE INDEX users_phone_idx ON users (phone)`
- **Impact**: momentary lock on 310,000 rows only, so lock cost is not the risk; the risk is the deploy-order incompatibility in F1
- **Mitigation**: none for the lock; keep the rename out of a period with long transactions so the momentary `ACCESS EXCLUSIVE` does not queue
- **Verification**: `\d users` shows `phone_number` and `users_phone_idx` still attached
- **Approval**: none
- **Reversible**: yes

## Safer migration path
1. Add the new column without a rewrite: `ALTER TABLE users ADD COLUMN phone_number varchar(32);`
2. Deploy code that writes both `phone` and `phone_number` and still reads `phone`.
3. Backfill in batches with an idempotent predicate:
   `UPDATE users SET phone_number = phone WHERE phone_number IS NULL AND phone IS NOT NULL;`
4. Deploy code that reads `phone_number` only, and update the CronJob image and spec.
5. After one full deploy cycle, drop the old column in a separate gated migration:
   `ALTER TABLE users DROP COLUMN phone;`

## Rollback and recovery
- **Schema rollback**: `ALTER TABLE users RENAME COLUMN phone_number TO phone;` restores the old name instantly and keeps every value.
- **Data recovery**: a rename moves no data, so nothing is lost; the recovery problem is code, not data.
- **Application rollback**: rolling the application back after the rename fails against the new schema, because the old image reads `phone`; roll the schema back first.

## Pre-deploy checklist
- [ ] Deploy order decided: expand-and-contract, or a documented stop-the-world window
- [ ] CronJob image and spec updated to `phone_number`
- [ ] Old pods fully drained before any direct rename
- [ ] API consumers notified of the JSON key change