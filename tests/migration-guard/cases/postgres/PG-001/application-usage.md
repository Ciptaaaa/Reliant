# Application usage: `users.phone`

## Direct reads and writes (still on `main`)

- `app/controllers/users_controller.rb:14`
  ```ruby
  permitted = params.require(:user).permit(:email, :phone, :display_name)
  ```
- `app/controllers/users_controller.rb:31`
  ```ruby
  render json: @user.slice(:id, :email, :phone, :display_name)
  ```
  → the profile endpoint returns `phone` in the API response body.
- `app/models/user.rb:9`
  ```ruby
  validates :phone, format: { with: /\A\+?[0-9 ]{7,20}\z/ }, allow_blank: true
  ```
- `app/services/two_factor/send_code.rb:22`
  ```ruby
  return unless user.phone.present?
  SmsGateway.deliver(to: user.phone, body: code)
  ```
  → phone is the *only* 2FA recovery channel for SMS users.

## Batch / external consumers

- `lib/tasks/export_support.rake:17`
  ```ruby
  User.where.not(phone: nil).pluck(:id, :email, :phone).each { |row| Csv.append(row) }
  ```
  Nightly cron at 02:00 UTC, writes to `s3://support-exports/users/`.
- Partner integration: `docs/integrations/crm-sync.md:8` states the CRM pulls `/api/v1/users` and
  uses `phone` as the primary contact field.

## Views / DB-side dependencies

- `db/schema.sql` declares `users_phone_idx` on `phone`; dropping the column drops the index.
- No database views reference `phone`.

## Not searched

- Mobile clients (separate repository, not present here).
- BI dashboards (Metabase, separate instance).

## Affected attribute

Every row above reads or writes the attribute `phone` on the `users` table.
