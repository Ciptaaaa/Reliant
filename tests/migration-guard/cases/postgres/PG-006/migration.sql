-- db/migrate/20261116100000_add_orders_status_idx.rb  (SQL form shown)
--
-- Rails wraps this migration in a transaction by default; there is no
-- `disable_ddl_transaction!` in the migration class.

CREATE INDEX CONCURRENTLY orders_status_idx ON orders (status);
