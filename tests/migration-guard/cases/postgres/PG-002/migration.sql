-- prisma/migrations/20261116091500_orders_status_not_null/migration.sql
ALTER TABLE orders ALTER COLUMN status SET NOT NULL;
