# Application usage: `users.phone` (current, deployed code)

## Reads / writes in the deployed version

- `app/controllers/users_controller.rb:31`
  ```ruby
  render json: @user.slice(:id, :email, :phone, :display_name)
  ```
- `app/services/two_factor/send_code.rb:22` — `SmsGateway.deliver(to: user.phone, ...)`
- `app/models/user.rb:9` — `validates :phone, ...`

## The new version (on the migration branch, not yet deployed)

- `app/models/user.rb` now declares `attribute :phone_number, :string`.
- `app/controllers/users_controller.rb` permits and serializes `:phone_number`.
- `app/services/two_factor/send_code.rb` uses `user.phone_number`.

## Background jobs

- `lib/tasks/export_support.rake:17` — `User.where.not(phone: nil).pluck(:id, :email, :phone)`.
  This job runs as a Kubernetes CronJob built from the **last released image**, so it keeps using
  `phone` until a new image is released and the CronJob spec is updated.

## Database-side dependencies

- `users_phone_idx` is on `phone`. PostgreSQL renames the column and keeps the index attached (the
  index name is unchanged), so query plans are preserved.
- No database views reference `phone`.

## Not searched

- Mobile clients (they call the API, so they follow the API field name; the API renames the JSON key
  in the new version, which is an API-contract change outside this skill's scope).
