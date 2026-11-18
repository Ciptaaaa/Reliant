# Application usage: `payments.method`

## Writes

- `app/Services/Checkout/CapturePayment.php:52`
  ```php
  Payment::create(['order_id' => $order->id, 'method' => $gatewayMethod, 'amount_cents' => $amount]);
  ```
  → always sets `method` for new payments.
- `app/Console/Commands/ImportLegacyPayments.php:71` — bulk insert from the old gateway export; the
  export omits `method` for 2 columns of legacy data (so `NULL` can still be written by this path).

## Reads

- `app/Reports/MethodBreakdown.php:19`
  ```php
  DB::table('payments')->selectRaw("COALESCE(method,'unknown') AS method, COUNT(*) c")->groupBy('method');
  ```
  → reporting on the **replica**, tolerant of `NULL`.
- `resources/views/admin/payments/show.blade.php:33` — displays `{{ $payment->method }}` (empty for NULL).

## Replication

- The replica is used for reporting; large DDL on the primary produces replication lag.

## Not searched

- The legacy gateway export file contents (external).
