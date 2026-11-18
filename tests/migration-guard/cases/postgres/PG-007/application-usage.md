# Application usage: `users.locale`

- **Existing code references**: none. The column does not exist yet and no code path reads or writes
  `locale` (`grep -r "locale" app/ lib/ config/` returns only the i18n configuration, unrelated to
  the users table).
- **Planned usage**: a later release will read `user.locale` to choose a default interface language;
  absent values fall back to `en`. The plan is recorded in `docs/rfc/0042-localisation.md:12`.
- `app/models/user.rb` declares a strict attribute list (no `locale`); until the later release runs,
  nothing selects it.

## Not searched

- Mobile clients (not affected: the API does not expose `locale` until the later release).
