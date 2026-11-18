-- Current schema (MySQL 8.0.36, InnoDB)
-- Source: database/schema/mysql-schema.sql @ 2026-11-14

CREATE TABLE `accounts` (
  `id`            BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  `email`         VARCHAR(255)    NOT NULL,
  `display_name`  VARCHAR(120)    NOT NULL,
  `password_hash` VARCHAR(255)    NOT NULL,
  `created_at`    DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (`id`),
  UNIQUE KEY `accounts_email_unique` (`email`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

-- Rows: ~2,400,000. No generated columns, no partitioned table.
