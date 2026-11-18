-- database/migrations/2026_11_16_123000_payments_order_fk.php  (raw SQL form)
ALTER TABLE `payments`
  ADD CONSTRAINT `payments_order_id_fk` FOREIGN KEY (`order_id`) REFERENCES `orders` (`id`);
