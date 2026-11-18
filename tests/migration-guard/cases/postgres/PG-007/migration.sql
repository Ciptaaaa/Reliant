-- db/migrate/20261116103000_add_locale_to_users.sql
ALTER TABLE users ADD COLUMN locale varchar(10);
