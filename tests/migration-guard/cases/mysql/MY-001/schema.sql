-- Current schema (MySQL 8.0.36, InnoDB)
-- Source: database/schema.sql @ 2026-11-10

CREATE TABLE `suppliers` (
  `id`            BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  `name`          VARCHAR(160)    NOT NULL,
  `contact_email` VARCHAR(255)    DEFAULT NULL,
  `contact_phone` VARCHAR(32)     DEFAULT NULL,
  `created_at`    TIMESTAMP       NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (`id`),
  KEY `suppliers_contact_phone_idx` (`contact_phone`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

-- Rows: ~4,800. About 3,100 rows have a non-NULL contact_phone.
-- contact_phone is the phone number printed on purchase order PDFs and
-- exported to the ERP nightly (see application-usage.md).
