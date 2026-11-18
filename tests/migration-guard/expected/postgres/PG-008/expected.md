# PG-008 — Expected analysis

## Expected verdict

**CONDITIONAL** — the rename is instant but breaks every running version that still reads `phone`.

## Findings

### F1 — `HIGH` — `rename` — deploy-order incompatibility during rolling deploy

`ALTER TABLE users RENAME COLUMN phone TO phone_number` completes immediately, but from that moment
`phone` no longer exists. The migration runs **before** the new pods start (`context.md`), and during
the rolling deploy the old pods still execute:

- `app/controllers/users_controller.rb:31` — `slice(:phone)` fails with
  `ActiveModel::MissingAttributeError`/`PG::UndefinedColumn` for the profile endpoint.
- `app/services/two_factor/send_code.rb:22` — `user.phone` raises; SMS 2FA stops.
- `lib/tasks/export_support.rake:17` — the CronJob image is the last release, so the nightly export
  keeps querying `phone` and fails.

`context.md` confirms a rolling deploy where old and new versions run simultaneously, so this is not
hypothetical.

**Evidence required:** `application-usage.md` (controller, 2FA, CronJob), `migration.sql`,
`context.md` (rolling deploy)

**Mitigation:** expand and contract, not rename:

1. `ALTER TABLE users ADD COLUMN phone_number varchar(32);` (metadata-only, `LOW`).
2. Deploy code that writes both `phone` and `phone_number` and reads `phone`.
3. Backfill `phone_number` from `phone` in batches; verify counts.
4. Deploy code that reads `phone_number` only.
5. After one full deploy cycle, in a separate gated migration: `DROP COLUMN phone`.
6. Update the CronJob image and spec so the export uses `phone_number` before step 5.

If the team insists on a rename, the deploy must stop the world (stop the old version fully, rename,
start the new version), which must be explicitly approved and has a downtime cost.

**Approval: schedule (F1)** — a rename loses no data, so no one has to accept data loss. What it needs is a deploy schedule: expand-and-contract, or a documented stop-the-world window.
written is a breaking deploy.

### F2 — `LOW` — `lock-and-rewrite` — instantaneous `ACCESS EXCLUSIVE`

The rename takes `ACCESS EXCLUSIVE` momentarily. Table size does not matter: it is a catalog update,
and the attached `users_phone_idx` follows the column (index name unchanged). Lock impact alone is
`LOW` — the reason this migration is risky is compatibility, not locking.

**Evidence required:** `migration.sql`, `schema.sql`.

**Mitigation:** optional `lock_timeout`.

### F3 — `MEDIUM` — `application-compatibility` — API response field disappears

The profile endpoint returns `phone` today. After the rename, the JSON key changes to `phone_number`
for API consumers. This is an API-contract break, outside the migration's main risk but caused by it;
record it as a downstream compatibility item and coordinate with API clients.

**Evidence required:** `application-usage.md` (controller serializer).

### F4 — `INFO` — `no-risk-observed` — no data loss from the rename itself

`RENAME COLUMN` does not rewrite or remove data. Rolling the schema back (rename to `phone`) restores
the structure exactly, so schema rollback is clean here. Note that rolling back the schema while new
code is running causes the mirror-image breakage.

## Unknowns that must be reported

- Whether the deploy stops the old version before running migrations is not stated; `context.md` says
  migrations run before new pods start, which implies old pods keep running.
- Mobile clients' handling of the changed JSON key is not inspected.

## Verdict reasoning

Instant DDL, but proven breakage of running code during the deploy → `HIGH` → `CONDITIONAL`. A report
that says `APPROVED` because "RENAME is instant" is the exact mistake this case detects.
