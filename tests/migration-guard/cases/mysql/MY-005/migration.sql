-- database/migrations/2026_11_16_124000_invoices_currency_varchar.php  (raw SQL form)
ALTER TABLE `invoices`
  MODIFY COLUMN `currency` VARCHAR(3) NOT NULL,
  ALGORITHM=INSTANT, LOCK=NONE;
