-- db/migrations/0042_users_email_unique.sql
ALTER TABLE users ADD CONSTRAINT users_email_key UNIQUE (email);
