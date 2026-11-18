-- database/migrations/2026_11_16_130000_accounts_add_nickname.php  (raw SQL form)
ALTER TABLE `accounts`
  ADD COLUMN `nickname` VARCHAR(80) DEFAULT NULL,
  ALGORITHM=INSTANT, LOCK=DEFAULT;
