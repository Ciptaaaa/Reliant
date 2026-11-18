-- Current schema (MySQL 8.0.36, InnoDB)
-- Only the tables that already exist.
CREATE TABLE `accounts` (
  `id`    BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  `email` VARCHAR(255)    NOT NULL,
  PRIMARY KEY (`id`),
  UNIQUE KEY `accounts_email_unique` (`email`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;
