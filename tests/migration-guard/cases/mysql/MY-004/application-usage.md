# Application usage: `payments.order_id` and order deletion

## Reads

- `app/Models/Order.php:34` — `hasMany(Payment::class)`; report screens list payments per order. Orders
  without payments are normal; payments without orders are **not** expected in the UI.

## Writes

- `app/Services/Checkout/CapturePayment.php:47` — creates a payment with a validated `order_id`.
- `app/Console/Commands/ReconcileGateway.php:63` — inserts payments from a gateway settlement file,
  keyed by `order_id` taken from the file; does **not** check that the order exists locally.

## Order deletion (the thing the developer mentioned)

- `app/Services/Orders/DeleteOrder.php:28`
  ```php
  DB::transaction(function () use ($order) {
      $order->payments()->delete();   // delete payments first
      $order->forceDelete();          // then the order
  });
  ```
  → delete order works today only because payments are deleted first. With an FK using the default
  action (`RESTRICT`/`NO ACTION`), deleting a payment row is fine, but **any** code path that deletes an
  order without first deleting payments starts failing with `ERROR 1451 (Cannot delete or update a
  parent row)`. There is no `ON DELETE CASCADE` anywhere.

- `app/Services/Orders/ArchiveOldOrders.php:41` — moves old orders to `orders_archive` and deletes from
  `orders` **without** deleting payments (they keep pointing at the archived ids). This path breaks
  under an FK.

## Not searched

- The gateway settlement file (external).
