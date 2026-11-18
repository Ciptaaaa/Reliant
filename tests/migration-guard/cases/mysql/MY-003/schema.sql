-- Current schema (MySQL 8.0.36, InnoDB)
-- Source: database/schema/mysql-schema.sql @ 2026-11-14

CREATE TABLE `customers` (
  `id`             BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  `name`           VARCHAR(160)    NOT NULL,
  `reference_code` VARCHAR(32)     NOT NULL,
  `created_at`     DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (`id`),
  KEY `customers_reference_code_idx` (`reference_code`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

-- Data facts (developer ran on 2026-11-16):
--   SELECT COUNT(*) FROM customers;                                                  -> 618,904
--   SELECT COUNT(*) FROM (
--     SELECT reference_code FROM customers GROUP BY reference_code HAVING COUNT(*) > 1
--   ) d;                                                                             -> 2,918  -- exact duplicates
--   SELECT COUNT(*) FROM (
--     SELECT UPPER(reference_code) FROM customers
--     GROUP BY UPPER(reference_code) HAVING COUNT(*) > 1
--   ) d;                                                                             -> 3,067  -- case-insensitive duplicates
-- The collation utf8mb4_0900_ai_ci is case-insensitive, so the unique index will fail on 3,067
-- groups, not 2,918.
