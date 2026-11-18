# Migration Guard Report — MY-008

## Verdict: **BLOCK** — not safe to merge as written

The `ALGORITHM=INSTANT` makes the DDL itself fast, but it does not make the *deploy* safe. This migration removes the `mobile` column while multiple consumers still read it, and it precedes the code that reads `mobile_phone`.

## Evidence and reasoning

**1. Migration runs before new code; old code reads `mobile`.**
From `context.md`:
> The migration runs before the new application tasks start. Old tasks continue serving for several minutes during the rolling update.

After the rename commits, `mobile` no longer exists. Any old task still serving will hit the deployed code, which reads `mobile`:

- `CustomerController.php:52` → `$customer->only(['id', 'name', 'email', 'mobile'])`
- `SendVerification.php:31` → `$this->sms->send($customer->mobile, $code)`

These produce "Unknown column 'mobile'" errors for the duration of the rolling update (several minutes). SMS verification and customer API responses break on every old task.

**2. The nightly cron cannot be fixed by this release alone.**
- `ExportCustomers.php:26` explicitly selects `mobile` from `DB::table('customers')`.
- `application-usage.md`:
  > runs from the **last released image** in the scheduler; keeps selecting `mobile` until a new image is released and the scheduler updated.

So the cron fails its next run unless the scheduler image is updated in lockstep — which is not the stated plan.

**3. "Instant" is not "compatible."**
The `ALGORITHM=INSTANT` only describes the engine's DDL cost. `context.md` flags the exact trap:
> The agent should verify the version specific behaviour and not conflate "instant DDL" with "safe for a rolling deploy".

Correct engine detail: on MySQL 8.0.36 the `RENAME COLUMN ... ALGORITHM=INSTANT` is valid and, per the notes, keeps `customers_mobile_idx` attached with its name unchanged, so plans are preserved. None of that addresses readers of the old name.

**4. Unverified API contract change.**
`application-usage.md` under "Not searched":
> Mobile app clients that receive `mobile` in the JSON response (API contract change).

The branch controller switches the response key to `mobile_phone`. Even after the DB is fine, mobile clients parsing `mobile` may break. This needs an explicit client-compatibility decision (e.g., return both keys for a deprecation window).

## Required changes before this can be approved

Make the rename across at least three deploys, expand-then-contract:

**Release 1 — add, don't rename.**
- `ALTER TABLE customers ADD COLUMN mobile_phone VARCHAR(32) DEFAULT NULL, ALGORITHM=INSTANT;`
- Backfill `mobile_phone = mobile` (batched, ~510k rows) — e.g., a separate, idempotent job.
- Add a trigger or write-path dual-write so new rows fill both columns.
- Deploy app code that *reads* `mobile_phone`, falls back to `mobile`, and writes both. Return both JSON keys (`mobile` and `mobile_phone`) so mobile clients keep working.
- Confirm the scheduler image is updated to read whichever column the code supports (dual-read preferred).

**Release 2 — verify and cut over.**
- Confirm no writes to `mobile` only, `mobile_phone` fully populated, clients migrated off `mobile` (check logs/telemetry if available).
- Remove the old JSON key after the deprecation window.

**Release 3 — drop (contract).**
- `ALTER TABLE customers DROP COLUMN mobile, ALGORITHM=INSTANT;` (drop index is part of dropping the column).
- Optionally rename to keep the `customers_mobile_idx` name aligned — but this is cosmetic.

If a straight rename is *mandated*, an alternative is a coordinated maintenance window / traffic drain that stops all old tasks and the cron before migrating, then starts only new code. That conflicts with the stated ECS rolling + nightly-cron model, so the expand/contract path is the real fix.

## Summary

| Check | Result |
|---|---|
| Engine correctness of `ALGORITHM=INSTANT` RENAME COLUMN on 8.0.36 | ✅ Valid |
| Index preserved | ✅ Confirmed by notes |
| Generated columns / FKs on `mobile` | ✅ None |
| Old code compatible during rolling update | ❌ Breaks |
| Nightly cron compatible | ❌ Breaks |
| Mobile client API contract | ❌ Not searched, key removed |

**Verdict: BLOCK.** The migration is engine-instant but deploy-unsafe. Use an expand/contract sequence (add → backfill + dual-read/write → verify → drop) or an explicit full traffic drain, and resolve the API contract change before merging.