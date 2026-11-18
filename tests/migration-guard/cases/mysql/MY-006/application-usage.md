# Application usage: `shipments` and long-running transactions

## Reads

- `app/Http/Controllers/ShipmentTrackingController.php:19` — `SELECT … WHERE id = ?` per request.
  In production this is the highest-QPS query on the table.

## Long-running transaction on the primary

- `app/Console/Commands/SettlementReport.php:58`
  ```php
  DB::transaction(function () {
      DB::table('shipments')->where('shipped_at', '<', $cutoff)->lockForShare()->get();
      // … 2–6 minutes of aggregation in PHP …
  });
  ```
  Runs every 15 minutes **on the primary** (no read replica routing configured for this command). It
  holds a shared metadata lock on `shipments` for the whole 2–6 minutes.

## Writes

- `app/Services/Shipping/CreateShipment.php:29` — inserts new shipments with `status` (the new code).
- Older deployed code inserts without `status`; the new default covers it.

## Not searched

- The analytics replica job (runs on the replica, not the primary).
