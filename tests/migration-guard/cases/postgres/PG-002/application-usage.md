# Application usage: `orders.status`

## Reads

- `src/api/orders/list.ts:23`
  ```ts
  prisma.order.findMany({ where: { customerId }, select: { id: true, status: true, totalCents: true } });
  ```
- `src/api/orders/report.ts:41` — groups by `status`, includes a `null` bucket in the dashboard.

## Writes

- `src/domain/orders/create.ts:18`
  ```ts
  await prisma.order.create({ data: { customerId, status: null, totalCents } });
  ```
  → **new orders are inserted with `status = null`** until the fulfilment worker sets it.
- `src/jobs/fulfilment.ts:57` — `UPDATE orders SET status = 'fulfilled' WHERE id = $1`.
- `src/imports/legacy-shop.ts:12` — legacy importer, inserts rows without `status`.

## Consumers outside this repository

- Finance reporting queries `orders.status` and treats `NULL` as "unknown"; it is expected to keep
  producing rows with `NULL` status until the importer is retired.
- No application code writes an empty string for `status`.

## Not searched

- The fulfilment worker's deployment version; it may lag behind the API.
