# PG-006 — `CREATE INDEX CONCURRENTLY` inside a transaction

## Context

- **Engine**: PostgreSQL 15
- **Application**: Rails 7.1 with `strong_migrations` **not** installed; the team uses default Rails
  DDL transaction behaviour
- **What the developer said**:
  > The orders list page is slow when filtering by `status`. I want to add an index. I read that
  > `CONCURRENTLY` is the safe way, so I wrote:
  > `CREATE INDEX CONCURRENTLY orders_status_idx ON orders (status);`
  > The migration runner wraps each migration in a transaction. Is this ready to ship?
- **Table**: `orders`, ~9M rows, write-heavy.

## Deployment facts

- Rails runs each migration file inside `BEGIN … COMMIT` by default.
- `db:migrate` runs automatically in the deploy step against the primary.
- No `disable_ddl_transaction!` in the migration.

## Why this case exists

`CREATE INDEX CONCURRENTLY` cannot run inside a transaction block: PostgreSQL rejects it with
`CREATE INDEX CONCURRENTLY cannot run inside a transaction block`. The agent must identify that the
transaction wrapper makes this fail, and must also warn about the invalid index left behind *if* the
statement had been run outside a transaction and failed. This is a `HIGH` case: the deploy fails, but
no data is lost.
