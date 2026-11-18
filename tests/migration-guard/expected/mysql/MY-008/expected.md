# MY-008 — Expected analysis

## Expected verdict

**CONDITIONAL** — instant DDL, but it breaks the old application version during the rolling update and
the nightly cron.

## Findings

### F1 — `HIGH` — `rename` — deploy-order incompatibility during the rolling update

`RENAME COLUMN mobile TO mobile_phone` completes immediately, but old tasks keep running until the
rolling update finishes. During that window:

- `app/Http/Controllers/CustomerController.php:52` — `$customer->only([... 'mobile'])` returns an
  incomplete payload or raises a "column not found" error depending on Laravel/PDO error handling.
- `app/Services/Sms/SendVerification.php:31` — `$customer->mobile` raises; SMS verification stops.
- `app/Console/Commands/ExportCustomers.php:26` — the scheduler image is the last release, so the nightly
  export keeps selecting `mobile` and fails.

**Evidence required:** `application-usage.md` (controller, SMS service, cron export), `migration.sql`,
`context.md` (migration runs before new tasks start).

**Mitigation:** expand and contract:

1. `ALTER TABLE customers ADD COLUMN mobile_phone VARCHAR(32) DEFAULT NULL, ALGORITHM=INSTANT, LOCK=NONE;`
2. Deploy code that writes both and reads `mobile`.
3. Backfill `mobile_phone` from `mobile` in batches; verify counts.
4. Deploy code that reads `mobile_phone` only.
5. Update the scheduler image/spec to use `mobile_phone`.
6. After one full deploy cycle, in a separate gated migration: `ALTER TABLE customers DROP COLUMN mobile;`.

If the team insists on a true rename with zero old readers, it needs a stop-the-world deploy and
explicit approval.

**Human approval required:** yes — the eventual `DROP COLUMN mobile` is destructive.

### F2 — `LOW` — `lock-and-rewrite` — instant DDL, metadata-only

On MySQL 8.0.28+, `RENAME COLUMN` is instant (`ALGORITHM=INSTANT` supported); index
`customers_mobile_idx` stays attached to the column and is preserved. Table size does not matter. Lock
impact alone is negligible.

**Evidence required:** `migration.sql`, `schema.sql`.

**Mitigation:** none needed for locking.

### F3 — `MEDIUM` — `application-compatibility` — API field renamed

The customer endpoint returns `mobile` in JSON today and `mobile_phone` after the rename. API consumers
(the mobile app) see the field disappear. Coordinate with client releases or add `mobile_phone` while
keeping `mobile` for one release.

**Evidence required:** `application-usage.md` (CustomerController).

### F4 — `INFO` — `no-risk-observed` — no data loss

`RENAME COLUMN` does not rewrite or remove data; rolling the schema back (rename to `mobile`) restores
the structure exactly.

## Unknowns that must be reported

- Whether old tasks are drained before migrations run is not stated; `context.md` says migrations run
  before new tasks start, implying old tasks are still serving.
- The mobile app's handling of the changed JSON key is not inspected.

## Verdict reasoning

Instant DDL but proven breakage of running code → `HIGH` → `CONDITIONAL`. Approving because "RENAME is
instant" is the error this case detects.
