-- Current schema (MySQL 8.0.36, InnoDB)
-- Source: database/schema/mysql-schema.sql @ 2026-11-14

CREATE TABLE `orders` (
  `id`         BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  `placed_at`  DATETIME        NOT NULL,
  PRIMARY KEY (`id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE TABLE `payments` (
  `id`           BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  `order_id`     BIGINT UNSIGNED NOT NULL,
  `amount_cents` INT UNSIGNED    NOT NULL,
  `captured_at`  DATETIME        NOT NULL,
  PRIMARY KEY (`id`),
  KEY `payments_order_id_idx` (`order_id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

-- FK status: none on payments.order_id.
-- Data facts (developer ran on 2026-11-16):
--   SELECT COUNT(*) FROM payments;                                            -> 11,204,882
--   SELECT COUNT(*) FROM payments p
--     LEFT JOIN orders o ON o.id = p.order_id WHERE o.id IS NULL;            -> 47,308  -- orphans
-- Orphans come from a 2021 migration that copied orders to a new table and re-keyed ids.
