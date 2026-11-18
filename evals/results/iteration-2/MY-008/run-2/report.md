# Migration Guard Report — MY-008

## Verdict: ❌ BLOCK — not safe to merge/deploy as written

The DDL itself is engine-fast, but the migration is **not compatible with the rolling deployment**. It will break running old code tasks, the old nightly cron, and likely mobile clients.

---

## 1. DDL correctness (engine)

`ALTER TABLE ... RENAME COLUMN mobile TO mobile_phone, ALGORITHM=INSTANT`

- **Syntax valid** on MySQL 8.0.36 (`RENAME COLUMN` is 8.0.28+). `ALGORITHM=INSTANT` is supported for pure rename — it's metadata-only, no table rebuild, no rows touched (620k rows irrelevant).
- **No type/attribute loss.** `RENAME COLUMN` (and even `CHANGE COLUMN` when only the name differs) keeps the existing definition; there is no "full definition" to accidentally alter here. The classic `CHANGE COLUMN`-with-wrong-definition trap does **not** apply to this statement.
- **Index preserved.** Evidence: *"`customers_mobile_idx` is attached to `mobile`. `RENAME COLUMN` keeps the index attached (index name is unchanged), so plans are preserved."* Index **name** remains `customers_mobile_idx` (name physically references the old word but stays functional). No generated columns or FKs reference `mobile` (confirmed in usage doc). ✅ Engine-wise OK.

So the developer is right that the rename *is instant*. That is **not** the same as *safe for a rolling deploy* — which is exactly the trap this case is about.

---

## 2. Rolling-deploy breakage (the real problem)

Migration runs **before** new tasks start; old tasks keep serving for several minutes (2 ECS tasks, rolling).

**Old code (deployed image) after rename → references `mobile`, which no longer exists:**

- `CustomerController.php:52` — `$customer->only(['id','name','email','mobile'])` → column gone → Eloquent `only()` returns without `mobile` / attribute lookup fails → **API response silently loses `mobile`** (or errors), breaking mobile app clients.
- `SendVerification.php:31` — `$this->sms->send($customer->mobile, $code)` → **`null` / "unknown column"** → SMS verification fails for every user during the window. This is user-facing and security-sensitive.

**Nightly cron (last released image) — evidence:** *"runs from the last released image in the scheduler; keeps selecting `mobile` until a new image is released and the scheduler updated."*

- `ExportCustomers.php:26` — `DB::table('customers')->select('id','name','mobile')->whereNotNull('mobile')...` → after rename this query **errors with `Unknown column 'mobile'`** and the entire nightly export fails until both a new image is built **and** the scheduler task is updated. The dependency window is not bounded by the few-minute rolling update — it extends to whenever the scheduler image is rebuilt.

**New code is fine** (already uses `mobile_phone`), but the deploy order guarantees old code runs against the new schema.

**Not searched / unresolved:** mobile app clients consuming `mobile` from the JSON API. The old controller returning `mobile` vs. new returning `mobile_phone` is an **API contract change**; clients pinned to `mobile` will break regardless of DDL. This was not searched — must be confirmed before any cutover.

---

## 3. Why "instant = no downtime" is wrong here

| Aspect | Status |
|---|---|
| DDL lock time | Instant ✅ |
| Old app read path (`$customer->mobile`) | ❌ breaks |
| SMS verification | ❌ breaks |
| Nightly export | ❌ breaks (extends past rolling window) |
| API clients | ⚠️ unverified contract change |
| New app (branch) | ✅ |

The migration is instantaneous for InnoDB but **breaks every reader of the old name** during the overlap window, so the *effective* downtime is the rolling-update window plus the scheduler-image lag — not zero.

---

## 4. Required changes before merge

Use the expand/contract (expand-migrate-contract) pattern instead of a single atomic rename:

1. **Expand (deploy first, additive):**
   ```sql
   ALTER TABLE customers
     ADD COLUMN mobile_phone VARCHAR(32) DEFAULT NULL, ALGORITHM=INSTANT;
   ```
   (optionally add a backfill + dual-write in app; keep `mobile` intact).
2. **Backfill** `mobile_phone = mobile` (batched), and have new code **dual-write** both columns / read fallback `mobile ?? mobile_phone`.
3. **Deploy new app** (reads `mobile_phone`, writes both). Let old tasks drain.
4. **Update the nightly cron image + scheduler** so `ExportCustomers` no longer selects `mobile`.
5. **Search clients** for `mobile` in the JSON contract; ship a client-compatible response (return both keys or version the API) before dropping the old key.
6. **Contract (only after all readers are gone):**
   ```sql
   ALTER TABLE customers DROP INDEX customers_mobile_idx, ALGORITHM=INPLACE; -- or keep index name
   ALTER TABLE customers DROP COLUMN mobile, ALGORITHM=INSTANT;
   ```
   (dropping a column is not `INSTANT` in all MySQL versions — verify; 8.0 supports `INSTANT` for drop in 8.0.29+ **only** under conditions; otherwise schedule an `INPLACE`/online DDL window. Rename the index to `customers_mobile_phone_idx` in a separate step.)

If a single-step rename is absolutely required, it must be **coordinated**: stop the old tasks and scheduler **before** running it, do a maintenance/freeze window, then start new tasks — which contradicts the stated "no downtime" rollout.

---

## 5. Evidence relied on (quoted)

- *"The migration runs before the new application tasks start. Old tasks continue serving for several minutes during the rolling update."*
- *"The nightly cron image is built from the last release."* / *"keeps selecting `mobile` until a new image is released and the scheduler updated."*
- Deployed `CustomerController.php:52`: `...only(['id', 'name', 'email', 'mobile'])`
- Deployed `SendVerification.php:31`: `$this->sms->send($customer->mobile, $code)`
- `ExportCustomers.php:26`: `...select('id', 'name', 'mobile')->whereNotNull('mobile')...`
- *"`customers_mobile_idx` is attached to `mobile`. `RENAME COLUMN` keeps the index attached..."* (engine side safe — confirms the problem is **application compatibility**, not DDL).
- *"Not searched — Mobile app clients that receive `mobile` in the JSON response (API contract change)."*

---

**Summary:** The `RENAME COLUMN ... ALGORITHM=INSTANT` statement is correct and fast at the engine level, preserves the index, and does not risk `CHANGE COLUMN` type-loss. **But it is not safe for this rolling deploy**: old app tasks, SMS verification, and the last-released nightly cron all still reference `mobile` and will fail, and the API contract change is unverified. Merge is **blocked** until an expand/contract migration (add `mobile_phone`, backfill, dual-write, deploy, update scheduler, search clients, then drop `mobile`) is used instead.