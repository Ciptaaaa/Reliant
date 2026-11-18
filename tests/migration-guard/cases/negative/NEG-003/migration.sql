-- database/migrations/2026_11_16_133000_create_invoice_drafts.php  (raw SQL form)
CREATE TABLE `invoice_drafts` (
  `id`          BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  `account_id`  BIGINT UNSIGNED NOT NULL,
  `payload`     JSON            NOT NULL,
  `created_at`  DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (`id`),
  KEY `invoice_drafts_account_id_idx` (`account_id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;
