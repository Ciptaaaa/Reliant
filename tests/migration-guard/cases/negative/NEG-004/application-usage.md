# Application usage: `invoices.currency`

- `app/serializers/invoice_serializer.rb:14` — will include `currency` once merged; existing serializer
  does not reference it and inserts do not specify it, so they receive `'USD'`.
- `lib/tasks/backfill_invoices.rake` — does **not** exist; no backfill is required because the default
  is stored in metadata and applied to existing rows on read.
- No index or constraint references `currency`.
