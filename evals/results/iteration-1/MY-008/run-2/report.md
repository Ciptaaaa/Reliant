# Migration Guard Report — MY-008

## Migration under review

`database/migrations/2026_11_16_131500_customers_rename_mobile.php` (raw SQL form)

```sql
ALTER TABLE `customers` RENAME COLUMN `mobile` TO `mobile_phone`, ALGORITHM=INSTANT;
```

## Verdict: ❌ BLOCK — do not merge as-is

The DDL itself is instantaneous, but it is **not safe for this deployment**. It is an immediately-breaking, non-expand/contract rename in a rolling-update environment with a second long-lived consumer (the cron). "Instant DDL" ≠ "safe for a rolling deploy."

---

## Evidence

### 1. Deployed (old) code reads the old name `mobile`

- `app/Http/Controllers/CustomerController.php:52`
  > `return response()->json($customer->only(['id', 'name', 'email', 'mobile']));`
- `app/Services/Sms/SendVerification.php:31`
  > `$this->sms->send($customer->mobile, $code);`

And from `context.md`:

> "The migration runs before the new application tasks start."
> "Old tasks continue serving for several minutes during the rolling update."

So between the moment this migration commits and the moment the **last** old task is drained, every request hitting an old task that touches `mobile` errors (unknown column in the `$customer->only([...])` projection and in `$customer->mobile`) — SMS verification and customer JSON both fail. This is a hard outage window measured in "several minutes," not zero.

### 2. The nightly cron is a second, independent consumer of `mobile`

- `app/Console/Commands/ExportCustomers.php:26` (in the branch) and the deployed image:
  > `DB::table('customers')->select('id', 'name', 'mobile')->whereNotNull('mobile')->chunk(1000, ...);`
- From `application-usage.md`:
  > "runs from the **last released image** in the scheduler; keeps selecting `mobile` until a new image is released and the scheduler updated."

This means the breakage window is **not bounded by the rolling update**. Even after both web tasks are on the new image, the scheduler keeps running the old image and will fail on the next nightly run (or any cron tick) until it is redeployed. The migration's "instant" nature gives no protection here.

### 3. `ALGORITHM=INSTANT` governs DDL mechanics, not application compatibility

The rename is a metadata-only change in MySQL 8.0.28+ and the index is preserved (`customers_mobile_idx` stays attached, index name unchanged, per `application-usage.md`), and there are no generated columns/FKs to worry about — so the *engine* cost is fine. But the option says nothing about the fact that the **column name is a public identifier** used by running code. The whole premise of the case ("the rename is instant, so no downtime") conflates those two ideas.

### 4. The API contract changes for unknown clients

- From `application-usage.md`, section **Not searched**:
  > "Mobile app clients that receive `mobile` in the JSON response (API contract change)."

The response key changes from `mobile` to `mobile_phone`. That's a breaking API change for any client not verified — out of scope for this migration, but it means the rename cannot simply be "done in code and schema together."

---

## What is actually fine (so the fix isn't over-engineered)

- `RENAME COLUMN` is correct and preferable to `CHANGE COLUMN ... <full definition>`; there is no type change or attribute loss risk here, and it is supported on 8.0.36.
- Index `customers_mobile_idx` survives the rename with its name unchanged → query plans preserved.
- No generated columns or FKs reference `mobile`.
- ~620k rows / ~510k non-NULL is small; the expand/contract below is cheap.

---

## Recommended remediation — expand / contract, not a rename-in-place

**Release 1 (this PR) — expand:**
1. Add the new column alongside the old, backfilled, with a trigger or dual-write to keep both in sync:
   ```sql
   ALTER TABLE `customers` ADD COLUMN `mobile_phone` VARCHAR(32) DEFAULT NULL, ALGORITHM=INSTANT;
   -- backfill in batches, then keep both in sync (dual-write in app, or BEFORE INSERT/UPDATE trigger)
   ALTER TABLE `customers` ADD KEY `customers_mobile_phone_idx` (`mobile_phone`);
   ```
2. Ship app code that **writes both** and **reads `mobile_phone` with fallback to `mobile`** (or reads whichever is populated). Roll out web tasks and the scheduler image. Deploy the nightly cron image too — do not let the scheduler stay on the old image.
3. For the API: return both keys (`mobile` and `mobile_phone`) for a deprecation window so unverified mobile clients don't break.

**Release 2 (later, after all consumers are on the new image and API clients have migrated) — contract:**
4. Remove the trigger/dual-write; make the app read `mobile_phone` only.
5. Then:
   ```sql
   ALTER TABLE `customers` DROP COLUMN `mobile`, ALGORITHM=INSTANT;
   ```
   (Drop the old index if it now duplicates `customers_mobile_phone_idx`.)

At no point does a running process see a column disappear out from under it.

---

## Summary table

| Check | Result |
|---|---|
| DDL correctness (8.0.36, `RENAME COLUMN`) | ✅ valid, instant |
| Index/plans preserved | ✅ index name unchanged |
| Old web tasks during rolling update | ❌ reads `mobile`, breaks |
| Nightly cron on last-released image | ❌ reads `mobile`, breaks beyond deploy window |
| API contract for unverified mobile clients | ⚠️ unverified breaking change |
| Rollback | ⚠️ rename-back is also instant but repeats the outage |
| **Overall** | **❌ BLOCK — use expand/contract** |

One-line version: **The rename is instant for InnoDB but not for the old code, the old cron image, or unknown API clients — this needs an expand/contract migration, not a rename in place.**