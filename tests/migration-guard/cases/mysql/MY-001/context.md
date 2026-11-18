# MY-001 — Drop a column that application code still uses

## Context

- **Engine**: MySQL 8.0.36, InnoDB, single primary (RDS)
- **Application**: Laravel 11, deployed with an ECS rolling update (2 tasks)
- **What the developer said**:
  > `suppliers.contact_phone` has not been used since we switched to a supplier contact form. Dropping it:
  > `ALTER TABLE suppliers DROP COLUMN contact_phone;`
  > It is a small table so the lock should be fine.
- **Table**: `suppliers`, ~4,800 rows, low write traffic.

## Deployment facts

- Old and new application tasks run simultaneously during the rolling update.
- MySQL DDL does not roll back; if the statement succeeds it is committed immediately.
- Nightly RDS automated backups, 5-minute point-in-time recovery window retained 7 days.

## Why this case exists

Same shape as PG-001, different engine. The `DROP COLUMN` is fast and the lock is trivial on 4,800
rows, so lock analysis alone would call this safe. It is a `BLOCKER` because the code still uses the
column. It also checks that the agent does not accept the user's "small table" framing as the risk
analysis.
