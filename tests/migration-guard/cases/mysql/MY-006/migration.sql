-- database/migrations/2026_11_16_125000_shipments_add_status.php  (raw SQL form)
ALTER TABLE `shipments`
  ADD COLUMN `status` VARCHAR(20) NOT NULL DEFAULT 'pending',
  ALGORITHM=INPLACE, LOCK=SHARED;
