# Application usage: `accounts.nickname`

- **Existing references**: none. `grep -rn "nickname" app/ database/ resources/` returns no results.
- **Planned**: a later release will read `nickname` in the profile header and fall back to
  `display_name` when it is `NULL` (see `docs/rfc/0051-profile-nickname.md`).

## Not searched

- Mobile clients (not affected: API does not expose `nickname` until the later release).
