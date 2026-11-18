# PG-003 — `UNIQUE` constraint with duplicate rows

## Context

- **Engine**: PostgreSQL 15
- **Application**: Django 5, rolling deploy on Kubernetes
- **What the developer said**:
  > Users could sign up twice with the same email because the race was never handled. I want to add a
  > unique constraint on `users.email`. The app already validates emails in the form, so there should
  > be no duplicates. Here is the migration:
  > `ALTER TABLE users ADD CONSTRAINT users_email_key UNIQUE (email);`
- **Table**: `users`, ~2.1M rows, high write traffic.

## Deployment facts

- There is a nightly dedupe script that ran for the first time last week; it is not yet reported
  clean.
- Application-level validation exists (`forms.py`), but there is no database constraint.
- The API is a monolith with 6 replicas behind a load balancer.

## Why this case exists

Duplicate rows make a `UNIQUE` constraint fail outright (`BLOCKER`), and even without duplicates an
unqualified `ADD CONSTRAINT UNIQUE` takes `ACCESS EXCLUSIVE` while it builds the index on 2.1M rows
(`HIGH`). A correct report must do both: say it is unsafe *now* and propose the concurrent-index path.
