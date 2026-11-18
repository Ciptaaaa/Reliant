-- db/migrate/20261116104500_rename_phone_to_phone_number.sql
ALTER TABLE users RENAME COLUMN phone TO phone_number;
