-- Current schema (MySQL 8.0.36, InnoDB)
-- Source: database/schema/mysql-schema.sql @ 2026-11-14

CREATE TABLE `payments` (
  `id`           BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  `order_id`     BIGINT UNSIGNED NOT NULL,
  `method`       VARCHAR(24)     DEFAULT NULL,
  `amount_cents` INT UNSIGNED    NOT NULL,
  `captured_at`  DATETIME        NOT NULL,
  PRIMARY KEY (`id`),
  KEY `payments_order_id_idx` (`order_id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

-- Data facts (developer ran on 2026-11-16):
--   SELECT COUNT(*) FROM payments;                         -> 11,240,911
--   SELECT COUNT(*) FROM payments WHERE method IS NULL;    -> 84,302
-- The NULL rows predate 2023-02 (previous gateway import). No CHECK constraint exists on method.
