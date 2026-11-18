# PG-001 — Expected analysis

## Expected verdict

**BLOCKED** — the column is still read and written by code that is on `main` and by consumers outside
the repository.

## Findings

### F1 — `BLOCKER` — `destructive-column-drop` — dropping a column still used by application code

The migration drops `users.phone` while three application code paths on `main` still use it:

- `app/controllers/users_controller.rb:14` — `users#update` still permits `:phone`.
- `app/controllers/users_controller.rb:31` — the profile endpoint serializes `:phone`.
- `app/models/user.rb:9` — validation still refers to `phone`.
- `app/services/two_factor/send_code.rb:22` — SMS 2FA delivery reads `user.phone`.

Once the column is gone, `slice(:phone)` raises `ActiveModel::MissingAttributeError` (or omits the
key depending on AR version) and the 2FA path loses its only SMS channel. The claim "unused" in
`context.md` is contradicted by the code.

**Evidence required in the report** (at least the controller and the 2FA service):

- `application-usage.md:14` (`permit(:email, :phone, ...)`)
- `application-usage.md:22` (`user.phone` for SMS delivery)
- `migration.sql:2` (`DROP COLUMN phone`)

**Mitigation:** do not drop now. Ship the application change first (stop writing, stop reading, stop
exporting), keep dual-read if needed, then drop in a later release after one full deploy cycle.
The nightly export and the CRM integration (`docs/integrations/crm-sync.md:8`) must be migrated to a
replacement field *before* the drop.

**Approval: accept-risk (F1)** — a human accepts the loss of the old values; a recovery plan and restore point are required.

### F2 — `HIGH` — `data-integrity` — data loss with no in-place recovery

`users.phone` holds approximately 240,000 non-NULL values. PostgreSQL `DROP COLUMN` makes the column
invisible and does not rewrite the table; the values are not recoverable through any schema rollback.
The only recovery path is the nightly `pg_dump` (24h RPO per `context.md`), which would also revert
unrelated tables to the backup point.

**Evidence required:** `migration.sql:2`; the row/value estimate in `schema.sql`.

**Mitigation:** if the drop must proceed, export the phone column to an archival location and record
the export before deploying; state the restore point and RPO in the report.

**Approval: accept-risk (F1)** — a human accepts the loss of the old values; a recovery plan and restore point are required.

### F3 — `MEDIUM` — `lock-and-rewrite` — `ACCESS EXCLUSIVE` lock and blocking deploy wait

`ALTER TABLE … DROP COLUMN` takes `ACCESS EXCLUSIVE`, conflicting with every `SELECT`. On a users table
of ~310k rows the statement itself is fast, but it queues behind any long-running transaction and
blocks all traffic on `users` while it waits. Because the index `users_phone_idx` is dropped with the
column, the drop also removes the supporting index.

**Mitigation:** set a short `lock_timeout` (e.g. `SET lock_timeout = '3s'`) and retry; check
`pg_stat_activity` for idle-in-transaction sessions first.

### F4 — `INFO` — `no-risk-observed` — parts examined and found safe

- No database views depend on `users.phone`, so no `CASCADE` chain is triggered by other database
  objects.
- `DROP COLUMN` does not rewrite the table, so there is no double-disk-space requirement.
- No foreign keys reference `phone`.

## Unknowns that must be reported

- Whether the mobile client or BI dashboards read `phone` (recorded as not searched in
  `application-usage.md`).
- Whether the CRM export can be changed before the drop; no evidence about the partner's timeline.

## Verdict reasoning

Rule 1 in `risk-classification.md`: evidence of breakage → `BLOCKER` → `BLOCKED`. Any verdict of
`APPROVED` or `CONDITIONAL` is wrong. A report that calls this `CONDITIONAL` because "the app change
just needs to ship first" is still wrong: the migration *as written* is unsafe today.
