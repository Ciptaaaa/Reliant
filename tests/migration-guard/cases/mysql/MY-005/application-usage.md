# Application usage: `invoices.currency`

## Writes

- `app/Services/Billing/CreateInvoice.php:55` — always writes a 3-letter ISO currency.
- `app/Imports/HistoricalInvoices.php:38` — writes currency from a legacy export; validated to be 3
  characters before insertion.

## Reads

- `app/Http/Resources/InvoiceResource.php:22` — returns `currency` as a string.
- `resources/views/invoices/pdf.blade.php:41` — prints `currency` with the amount.

## Indexes / constraints

- `currency` is not indexed and not part of any FK. No generated columns depend on it.

## Not searched

- Partner accounting system that receives invoice PDFs (external).
