-- database/migrations/2026_11_16_131500_customers_rename_mobile.php  (raw SQL form)
ALTER TABLE `customers` RENAME COLUMN `mobile` TO `mobile_phone`, ALGORITHM=INSTANT;
