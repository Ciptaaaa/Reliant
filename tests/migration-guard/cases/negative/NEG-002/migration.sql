-- db/migrate/20261116111000_add_orders_customer_placed_idx.py
#
# class AddOrdersCustomerPlacedIdx(migrations.Migration):
#     atomic = False          # <-- non-transactional, required for CONCURRENTLY
#     operations = [migrations.RunSQL(
#         "CREATE INDEX CONCURRENTLY orders_customer_placed_idx "
#         "ON orders (customer_id, placed_at DESC);"
#     )]
CREATE INDEX CONCURRENTLY orders_customer_placed_idx ON orders (customer_id, placed_at DESC);
