# MY-006 — Table rebuild and metadata lock

## Context

- **Engine**: MySQL 8.0.36, InnoDB, single primary, one async replica
- **Application**: Laravel 11; long-running analytics transaction every 15 minutes
- **What the developer said**:
  > We need a `status` column on `shipments` with a default. I wrote:
  > `ALTER TABLE shipments ADD COLUMN status VARCHAR(20) NOT NULL DEFAULT 'pending', ALGORITHM=INPLACE, LOCK=SHARED;`
  > It is just adding a column, so it should be fast.
- **Table**: `shipments`, ~6.5M rows.

## Deployment facts

- On MySQL 8.0, adding a column at the end with a constant default can use `ALGORITHM=INSTANT`, in which
  case only `LOCK=DEFAULT` is permitted. The developer has explicitly asked for `INPLACE` + `LOCK=SHARED`,
  which forces a rebuild and blocks writes for the duration instead of taking the instant path.
- There is a long-running settlement transaction on the **primary** (see `application-usage.md`) that
  holds a metadata lock on `shipments` for 2–6 minutes every 15 minutes.
- MySQL metadata locks are held for the duration of a transaction; a queued `ALTER` waiting for an MDL
  serialises later requests on the same table, which is the classic MDL pile-up.

## Why this case exists

Two layers of risk: (a) adding a column with a default on a modern MySQL can be instant, so the
developer's explicit `INPLACE`/`LOCK=SHARED` is *more* restrictive than necessary; and (b) the
`LOCK=SHARED` will still block writes and, worse, wait behind the settlement report transaction,
piling every new query behind it. The agent must identify the metadata-lock pile-up and recommend
either INSTANT add (verify on the actual version) or a lock-timeout/retry strategy, and must not
classify this as `LOW` just because it is "adding a column".
