# Application usage: `customers.reference_code`

## Writes

- `app/Services/Customers/GenerateReference.php:18` — fixed generator, writes uppercase.
- `app/Console/Commands/ImportCustomers.php:44` — imports from the partner CSV; **preserves the case
  from the CSV**, which is sometimes lowercase.
- `app/Http/Controllers/CustomerController.php:31` — reference looked up with `where('reference_code', $input)`;
  because the column collation is case-insensitive, `abc123` already matches `ABC123`.

## Cleanup tooling

- `app/Console/Commands/DedupeCustomers.php:27` — keeps the row with the lowest `id` per
  `reference_code` and deletes the rest. Requires a manual `--force`; last run 2026-10-26, before the
  generator fix.

## Not searched

- The partner CSV (external, regenerated weekly).
