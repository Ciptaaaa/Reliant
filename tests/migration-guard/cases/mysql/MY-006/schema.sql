-- Current schema (MySQL 8.0.36, InnoDB)
-- Source: database/schema/mysql-schema.sql @ 2026-11-14

CREATE TABLE `shipments` (
  `id`           BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  `order_id`     BIGINT UNSIGNED NOT NULL,
  `carrier`      VARCHAR(40)     NOT NULL,
  `tracking_no`  VARCHAR(64)     DEFAULT NULL,
  `shipped_at`   DATETIME        NOT NULL,
  `delivered_at` DATETIME        DEFAULT NULL,
  PRIMARY KEY (`id`),
  KEY `shipments_order_id_idx` (`order_id`),
  KEY `shipments_shipped_at_idx` (`shipped_at`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

-- Rows: ~6,500,000. Inserts: ~25/second.
