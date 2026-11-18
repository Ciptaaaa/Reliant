-- db/migrate/20261115093000_remove_phone_from_users.sql
ALTER TABLE users DROP COLUMN phone;
