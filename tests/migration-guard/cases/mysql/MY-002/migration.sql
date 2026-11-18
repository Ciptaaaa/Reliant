-- database/migrations/2026_11_16_121500_payments_method_not_null.php  (raw SQL form)
ALTER TABLE `payments` MODIFY COLUMN `method` VARCHAR(24) NOT NULL;
