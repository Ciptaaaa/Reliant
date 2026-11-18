-- db/migrate/20261116112000_add_currency_to_invoices.sql
ALTER TABLE invoices ADD COLUMN currency varchar(3) NOT NULL DEFAULT 'USD';
