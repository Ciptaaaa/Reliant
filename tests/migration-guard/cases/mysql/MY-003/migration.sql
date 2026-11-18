-- database/migrations/2026_11_16_122000_customers_reference_unique.php  (raw SQL form)
ALTER TABLE `customers`
  ADD UNIQUE KEY `customers_reference_code_unique` (`reference_code`),
  ALGORITHM=INPLACE, LOCK=DEFAULT;
