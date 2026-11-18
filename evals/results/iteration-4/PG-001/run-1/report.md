# Migration Guard Report

## Scope
- **Migration**: `db/migrate/20261115093000_remove_phone_from_users.sql`
- **Engine**: PostgreSQL 15
- **Inspected**: `migration.sql`, `schema.sql`, `application-usage.md`, `context.md`
- **Not inspected**: mobile clients (separate repository), BI dashboards (separate Metabase instance), the partner CRM itself
- **Unknowns**: the CRM's actual field mapping cannot be checked from this repository; it could hide a second breakage channel

## Verdict
**BLOCKED** — `phone` is still read by code on `main`, so dropping it breaks the profile endpoint, SMS 2FA recovery, and the nightly export on deploy.

## Findings

### F1 — BLOCKER — destructive-column-drop
- **Statement**: `ALTER TABLE users DROP COLUMN phone` in `migration.sql:2` — removes the column, its ~240,000 non-NULL values, and `users_phone_idx`
- **Evidence**:
  - `application-usage.md:5` — `app/controllers/users_controller.rb:14` permits `phone` on write
  - `application-usage.md:9` — `app/controllers/users_controller.rb:31` renders `slice(:email, :phone, :display_name)`, so the profile endpoint returns it
  - `application-usage.md:20` — `app/services/two_factor/send_code.rb:22` delivers the SMS code to `user.phone`, the only 2FA recovery channel for SMS users
  - `migration.sql:2` — the drop is unconditional; `context.md:17` states the phone-removal release has not been deployed and `main` still contains the phone code
- **Impact**: during the rolling deploy the old replicas raise `PG::UndefinedColumn` on the profile endpoint and on any write that permits `phone`; SMS users lose their only 2FA recovery channel
- **Mitigation**: retire every reader and writer first — permit list, serialisation, model validation, the 2FA path, and the nightly export — deploy that release, confirm the old replicas are drained, then drop the column in a separate migration
- **Verification**: `grep -rn "phone" app/ lib/ db/` in the deployed image returns no reader; the nightly export completes twice after the code deploy
- **Approval**: accept-risk
- **Reversible**: no

### F2 — HIGH — data-integrity
- **Statement**: the same `DROP COLUMN` destroys the ~240,000 stored phone numbers
- **Evidence**:
  - `schema.sql:15` — "Approximately 240,000 have a non-NULL phone"
  - `migration.sql:2` — the statement that removes them
  - `context.md:20` — the only restore path is a nightly `pg_dump` with a 24h RPO, restored roughly once a quarter
- **Impact**: re-adding the column restores the structure but every value returns `NULL`; recovery is a full restore that also reverts up to 24 hours of unrelated changes
- **Mitigation**: take a named `pg_dump` of `users` immediately before the drop and record the restore point beside the migration, so recovery does not depend on a quarterly restore
- **Verification**: the dump restores into a scratch database and `SELECT count(*) FROM users WHERE phone IS NOT NULL` returns ~240,000 there
- **Approval**: accept-risk
- **Reversible**: no

### F3 — MEDIUM — lock-and-rewrite
- **Statement**: the drop takes `ACCESS EXCLUSIVE` on `users` and drops `users_phone_idx` with the column
- **Evidence**:
  - `migration.sql:2` — a single `ALTER TABLE ... DROP COLUMN` with no `lock_timeout`
  - `schema.sql:12` — `CREATE INDEX users_phone_idx ON users (phone)`, removed implicitly with the column
  - `context.md:15` — rolling deploy with two replicas, no maintenance window stated
- **Impact**: the lock is brief on 310,000 rows, but it queues behind any long transaction and waits for in-flight queries, so a blocked attempt stalls the deploy step
- **Mitigation**: set `lock_timeout` in the migration session so a blocked attempt fails fast instead of queueing, and run it outside peak traffic
- **Verification**: the migration applies immediately or aborts on timeout; `pg_stat_activity` shows no long-running transaction on `users` at apply time
- **Approval**: schedule
- **Reversible**: yes

### F4 — INFO — no-risk-observed
- **Statement**: checked for database-side dependencies other than the index
- **Evidence**:
  - `application-usage.md:33` — "No database views reference `phone`"
  - `application-usage.md:27` — `lib/tasks/export_support.rake:17` is a cron job, covered by F1's mitigation
- **Impact**: none. There is no hidden view or generated column, so the risk is the application reads and the data loss, not a DB-side object.
- **Mitigation**: none
- **Verification**: `SELECT * FROM pg_depend` shows no view depending on `users.phone` after the drop
- **Approval**: none
- **Reversible**: yes

## Safer migration path
1. Deploy a release that stops reading and writing `phone`: `app/controllers/users_controller.rb:14`, `:31`, `app/models/user.rb:9`, `app/services/two_factor/send_code.rb:22`, `lib/tasks/export_support.rake:17`.
2. Tell the partner CRM named in `docs/integrations/crm-sync.md:8` that the field is going away, and confirm it has migrated.
3. Wait one full deploy cycle and confirm the old replicas are gone.
4. Take a named `pg_dump` of `users` and record the restore point.
5. In a separate migration, with a human approval attached:
   `ALTER TABLE users DROP COLUMN phone;`
6. Verify: `\d users` lists no `phone`, and the nightly export has completed twice.

## Rollback and recovery
- **Schema rollback**: `ALTER TABLE users ADD COLUMN phone varchar(32);` restores the structure and `users_phone_idx` can be recreated, but the column comes back empty.
- **Data recovery**: only from the named dump taken in step 4, or the nightly `pg_dump`; neither is a substitute for keeping the data.
- **Application rollback**: safe before step 5 because the code tolerates the column; after step 5, rolling the application back breaks the profile endpoint and SMS 2FA again.

## Pre-deploy checklist
- [ ] Human approval recorded for the `accept-risk` drop
- [ ] Every reader and writer retired and verified in the deployed release
- [ ] Partner CRM confirmed to no longer depend on `phone`
- [ ] Named `users` dump taken and restore-tested
- [ ] Old application replicas fully drained