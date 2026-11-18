# Migration Guard Report — MY-008

## Migration under review

```sql
-- database/migrations/2026_11_16_131500_customers_rename_mobile.php  (raw SQL form)
ALTER TABLE `customers` RENAME COLUMN `mobile` TO `mobile_phone`, ALGORITHM=INSTANT;
```

## Target

`customers` (InnoDB, MySQL 8.0.36), ~620,000 rows, ~510,000 with non-NULL `mobile`.

## Verdict

**BLOCK — not safe to merge as written.**

The DDL is instant *for the engine*, but it is not safe for a rolling deploy. It removes the `mobile`
column name that currently-deployed code and the nightly cron still depend on, and it changes an API
contract that has not been checked.

---

## Findings

### 1. High severity — breaks code running during the rolling update

Deployment facts: "The migration runs before the new application tasks start" and "Old tasks
continue serving for several minutes during the rolling update."

Deployed code references the old name:

- `app/Http/Controllers/CustomerController.php:52`
  ```php
  return response()->json($customer->only(['id', 'name', 'email', 'mobile']));
  ```
- `app/Services/Sms/SendVerification.php:31`
  ```php
  $this->sms->send($customer->mobile, $code);
  ```

After the rename, any query or model access that selects `mobile` fails with *Unknown column
'mobile'*. During the rolling update the old tasks are still serving live traffic (SMS verification
included), so they will error immediately after the migration commits. "Instant" describes lock time,
not compatibility.

**Fix direction:** use an expand/contract migration — add `mobile_phone`, dual-write/backfill, deploy
code that reads the new column (with fallback), wait for all old tasks to drain, then drop `mobile` in
a later release. Do not rename in one step while old code runs.

### 2. High severity — nightly cron selects `mobile` until a new image ships

`app/Console/Commands/ExportCustomers.php:26`
```php
DB::table('customers')->select('id', 'name', 'mobile')->whereNotNull('mobile')->chunk(1000, ...);
```
Evidence in the brief: "runs from the **last released image** in the scheduler; keeps selecting
`mobile` until a new image is released and the scheduler updated."

The cron is not part of the rolling application tasks, so it will break as soon as the rename lands
and stay broken until the scheduler image is rebuilt. The first night's export fails (or silently
returns nothing, depending on error handling) for ~510,000 rows' worth of data.

**Fix direction:** same expand/contract sequencing; migrate the cron query and ship a scheduler image
*before* `mobile` is removed.

### 3. Medium severity — unverified external API contract change

The brief lists under "Not searched": "Mobile app clients that receive `mobile` in the JSON response
(API contract change)." The branch now returns `mobile_phone` from `CustomerController.php`, so the
JSON key changes. Nothing in the evidence shows this was checked against mobile clients. Pinning
`mobile_phone` as a hard break risks older app versions reading a missing key.

**Fix direction:** confirm with the mobile team; either version the endpoint or emit both keys during
a deprecation window.

### 4. Low severity / verified OK — DDL mechanics

- `RENAME COLUMN` requires MySQL 8.0.28+; the server is **8.0.36**, so it is supported. ✅
- `ALGORITHM=INSTANT` is valid for `RENAME COLUMN` in this version; the rename itself does not rebuild
  the table. ✅
- `customers_mobile_idx` stays attached under its existing name: "`RENAME COLUMN` keeps the index
  attached (index name is unchanged), so plans are preserved." ✅
- No generated columns or foreign keys reference `mobile`. ✅
- This is a `RENAME COLUMN`, not `CHANGE COLUMN old new <definition>`, so there is no risk of
  silently changing the type or dropping attributes. ✅ (The trap called out in `context.md` does not
  apply to this statement.)

So the engine-level claim is correct — it is *only* the application-compatibility part that is wrong.

---

## What the developer got right / wrong

- Right: the DDL is instant and the index survives.
- Wrong: **"The rename is instant, so no downtime"** conflates instant DDL with deploy safety. Because
  the migration runs *before* new tasks start and old tasks keep serving, there is a real outage window
  for the app and a multi-day outage for the cron.

## Required before merge

1. Replace the single rename with expand/contract: add `mobile_phone`, dual-write, backfill 620k rows,
   verify, then drop `mobile` once *all* readers — two ECS tasks **and** the scheduler image — are on
   the new column.
2. Land the cron change and ship the scheduler image before removing `mobile`.
3. Resolve the mobile-client JSON contract (dual-emit or version).