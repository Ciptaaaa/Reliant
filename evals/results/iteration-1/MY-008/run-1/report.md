# Migration Guard Report — MY-008

**Verdict: ❌ BLOCK — not safe to merge as written.**

This is not a "technical DDL error" (the SQL is valid and `ALGORITHM=INSTANT` will work), it is a **deployment-safety / application-compatibility failure**. "Instant for the engine" ≠ "safe for a rolling deploy". The report below documents the failure mode, the evidence, and required changes.

---

## 1. Migration under review

```sql
ALTER TABLE `customers` RENAME COLUMN `mobile` TO `mobile_phone`, ALGORITHM=INSTANT;
```

- Target: `customers` (~620k rows), MySQL 8.0.36 InnoDB.
- `RENAME COLUMN` is metadata-only in 8.0.28+.
- **No `CHANGE COLUMN` type/attribute drift risk here** — the classic trap called out in `context.md` (accidentally changing column definition) is *avoided* by using `RENAME COLUMN`. ✅ on that specific axis.

---

## 2. Why it still fails

The migration **removes the name `mobile` entirely**. Any code still reading/writing `mobile` breaks with `Unknown column 'mobile'`.

### Hard evidence from `application-usage.md`

**a) Deployed (old) web code runs during the rolling update**
> "The migration runs before the new application tasks start."
> "Old tasks continue serving for several minutes during the rolling update."

Old code paths:
- `CustomerController.php:52` → `$customer->only(['id', 'name', 'email', 'mobile'])`
- `SendVerification.php:31` → `$this->sms->send($customer->mobile, $code)`

Both reference `mobile` on the `customers` model. The moment the migration commits, every old task executing these lines will fail (`SQLSTATE[42S22] Unknown column 'mobile'`). SMS verification is a **user-facing write path** — OTP sends will 500 for the duration of the rolling update (minutes).

**b) Nightly cron runs the *last released* image**
> "runs from the **last released image** in the scheduler; keeps selecting `mobile` until a new image is released."

`ExportCustomers.php:26`:
```php
DB::table('customers')->select('id', 'name', 'mobile')->whereNotNull('mobile')->chunk(1000, ...);
```
The scheduler image is **built from the last release**, so it will not switch to `mobile_phone` until a *separate* new image is released after this migration. Between migration commit and scheduler image update, the cron export **fails outright** (or silently exports nothing, depending on error handling). This is a second, longer-lived breakage beyond the web rolling window.

**c) Mobile app clients (API contract)**
> "Not searched — Mobile app clients that receive `mobile` in the JSON response."

`CustomerController.php:52` returns `mobile` in the JSON payload to mobile clients. The branch changes the key to `mobile_phone`. Even ignoring the DB, this is a **breaking API contract change** for already-installed app versions. If the DB rename lands before the API key is dual-published, clients that also call endpoints touching `mobile` fail server-side. This was explicitly left unverified — treated as an open risk, not a cleared one.

**d) Index behavior is fine (note, not blocker)**
> "`customers_mobile_idx` is attached to `mobile`. `RENAME COLUMN` keeps the index attached (index name is unchanged), so plans are preserved."

Confirmed — no query-plan concern. This does not offset the compatibility break.

---

## 3. Failure timeline (as written)

| Time | State | Effect |
|---|---|---|
| T0 | Migration commits: `mobile` gone, `mobile_phone` exists | — |
| T0 → T0+minutes | Old web tasks serve | Controller JSON + SMS verification throw `Unknown column 'mobile'` |
| T0 → next scheduler release | Nightly cron on old image | `ExportCustomers` throws / exports nothing |
| T0 → app-store rollout | Installed mobile clients | API `mobile` key change breaks consumers if server also reads `mobile` downstream |

The developer's premise — *"The rename is instant, so no downtime"* — conflates **DDL duration** (0s) with **application availability** (broken for the duration of rollout + cron lag). `context.md` flags this exact conflation as the case's trap.

---

## 4. Required changes before merge

1. **Do not use a bare rename.** Adopt the **expand/contract (parallel change)** pattern instead of renaming in place.

2. **Expand phase (this migration should be the expand, not the rename):**
   ```sql
   ALTER TABLE `customers`
     ADD COLUMN `mobile_phone` VARCHAR(32) DEFAULT NULL
       AFTER `mobile`,
     ALGORITHM=INSTANT;
   ```
   Then backfill `mobile_phone` from `mobile` in batch (or `CHANGE`/trigger), keeping `mobile` intact. Add index on `mobile_phone` as needed.

3. **Deploy dual-write / dual-read:** new code writes both `mobile` and `mobile_phone` and reads from `mobile_phone` with fallback to `mobile`. Ship this in a release. Ensure the **scheduler image is also updated** before the contract step — the cron dependency means the old-image lifetime is longer than the web rolling window; plan the contract phase to start only after the scheduler has run at least one full cycle on new code.

4. **API contract:** dual-publish both `mobile` and `mobile_phone` in the JSON response for a deprecation window; verify mobile clients (the "Not searched" item) before dropping `mobile` from the payload.

5. **Contract phase (separate later migration, only after cron + clients verified):** drop `mobile` and/or rename index; drop `customers_mobile_idx` or recreate as `customers_mobile_phone_idx` if desired.

6. **If a true `RENAME` is ever acceptable**, it can only happen after *all* consumers — old web tasks, the nightly cron image, and mobile clients — are confirmed to use `mobile_phone`. That is a future contract step, not this migration.

---

## 5. Summary

| Check | Result |
|---|---|
| SQL valid / correct MySQL 8.0.36 syntax | ✅ |
| `ALGORITHM=INSTANT` actually instant for rename | ✅ |
| `CHANGE COLUMN` type-drift trap avoided | ✅ |
| Index/plan preserved | ✅ |
| Safe during rolling update (old web tasks) | ❌ breaks `mail->...mobile` reads + SMS path |
| Safe for nightly cron on last-released image | ❌ `ExportCustomers` selects `mobile` |
| API contract for mobile clients verified | ⚠️ not searched — unresolved risk |

**Verdict: BLOCK. Replace the in-place `RENAME COLUMN` with an expand/contract migration; only rename/drop after old web tasks, the nightly cron image, and mobile clients are all confirmed off `mobile`.**