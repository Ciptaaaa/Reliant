# Application usage: `orders.status`

## Reads

- `app/controllers/orders_controller.rb:18`
  ```ruby
  @orders = Order.where(status: params[:status]).order(placed_at: :desc).limit(50)
  ```
  → the slow query this index is meant to fix.
- `app/jobs/status_report_job.rb:27` — `Order.where(status: 'pending').count` every 5 minutes.

## Writes

- `app/models/order.rb:44` — updates `status` on fulfilment.
- `app/controllers/orders_controller.rb:61` — inserts orders.

## Index writers

- No other migration creates or removes an index on `orders.status`.
- `db/structure.sql` has no index named `orders_status_idx`.

## Not searched

- Replica read paths (the primary handles all traffic in this deployment).
