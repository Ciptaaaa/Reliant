# Application usage: `signups.referral_source`

- No code references `referral_source`. `grep -rn "referral_source" app/ lib/ db/` returns nothing
  except this migration.
- Planned optional field on the signup form in a later release; existing rows stay `NULL`.
- `app/models/signup.rb` does not declare a strict attribute list that would reject extra columns.
