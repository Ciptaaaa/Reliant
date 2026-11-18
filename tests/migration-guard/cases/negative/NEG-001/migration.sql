-- db/migrate/20261116104000_add_referral_source_to_signups.sql
ALTER TABLE signups ADD COLUMN referral_source varchar(64);
