-- migrations/2026_11_16_add_orders_customer_fk.sql
ALTER TABLE orders
  ADD CONSTRAINT orders_customer_fk
  FOREIGN KEY (customer_id) REFERENCES customers (id);
