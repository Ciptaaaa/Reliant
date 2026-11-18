# Application usage: `orders(customer_id, placed_at)`

- `app/orders/views.py:41`
  ```python
  Order.objects.filter(customer_id=cid).order_by("-placed_at")[:50]
  ```
  → the query this index targets; currently uses `orders_customer_id_idx` then sorts.

## No destructive operations

- No migration in `db/migrate/` drops an index on `orders`.
- No code path depends on the index existing.
