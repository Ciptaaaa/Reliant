-- Current schema (MySQL 8.0.36, InnoDB)
-- Source: database/schema/mysql-schema.sql @ 2026-11-14

CREATE TABLE `invoices` (
  `id`          BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  `order_id`    BIGINT UNSIGNED NOT NULL,
  `currency`    CHAR(3)         NOT NULL,
  `total_cents` BIGINT UNSIGNED NOT NULL,
  `issued_at`   DATETIME        NOT NULL,
  PRIMARY KEY (`id`),
  KEY `invoices_order_id_idx` (`order_id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

-- Rows: ~18,400,000. Inserts: ~40/second at peak.
-- currency holds only ISO-4217 codes (3 chars) in every row.
