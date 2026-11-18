# Application usage: `users.email` uniqueness

## Validation (application layer only)

- `accounts/forms.py:22`
  ```python
  def clean_email(self):
      email = self.cleaned_data["email"]
      if User.objects.filter(email=email).exists():
          raise ValidationError("A user with this email already exists.")
      return email
  ```
  → a `SELECT` followed by an `INSERT`: a classic check-then-act race; it does not prevent duplicates
  under concurrency across 6 replicas.

## Writes

- `accounts/views.py:48` — `User.objects.create_user(email=..., ...)` inside a request handler.
- `accounts/management/commands/import_customers.py:31` — `bulk_create` from a CSV, `ignore_conflicts=False`.
  Import runs weekly and sources emails from a partner; casing is normalised only on `email.lower()`.

## Reads

- `auth_backends.py:12` — `User.objects.get(email__iexact=email)` — login lookup is case-insensitive.
  Two rows differing only in case would raise `MultipleObjectsReturned` at login.

## Not searched

- Any direct SQL from the analytics warehouse; not in this repository.
