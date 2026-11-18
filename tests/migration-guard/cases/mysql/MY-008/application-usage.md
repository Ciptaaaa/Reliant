# Application usage: `customers.mobile` (deployed) and `mobile_phone` (branch)

## Deployed code (running during the rolling update)

- `app/Http/Controllers/CustomerController.php:52`
  ```php
  return response()->json($customer->only(['id', 'name', 'email', 'mobile']));
  ```
- `app/Services/Sms/SendVerification.php:31`
  ```php
  $this->sms->send($customer->mobile, $code);
  ```
  → SMS verification uses `mobile`.

## Branch code (the new version, not yet deployed)

- `app/Http/Controllers/CustomerController.php` now returns `mobile_phone`.
- `app/Services/Sms/SendVerification.php` now uses `$customer->mobile_phone`.

## Nightly cron

- `app/Console/Commands/ExportCustomers.php:26`
  ```php
  DB::table('customers')->select('id', 'name', 'mobile')->whereNotNull('mobile')->chunk(1000, ...);
  ```
  → runs from the **last released image** in the scheduler; keeps selecting `mobile` until a new image
  is released and the scheduler updated.

## Database-side dependencies

- `customers_mobile_idx` is attached to `mobile`. `RENAME COLUMN` keeps the index attached (index name
  is unchanged), so plans are preserved.
- No generated columns or foreign keys reference `mobile`.

## Not searched

- Mobile app clients that receive `mobile` in the JSON response (API contract change).
