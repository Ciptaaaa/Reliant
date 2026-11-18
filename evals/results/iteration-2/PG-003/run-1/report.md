## Migration Guard Report — PG-003

**Verdict: BLOCKER — do not merge.**

---

### Blocker 1 — The migration will fail: duplicate rows exist right now

The developer's premise ("the app already validates emails in the form, so there should be no duplicates") is contradicted by the evidence.

From `schema.sql`:

> `SELECT count(*) FROM users GROUP BY email HAVING count(*) > 1` → **1,205 exact duplicates**

A `UNIQUE` constraint on `email` builds a unique B-tree index over every row; any duplicate key value makes it fail with:

```
ERROR: could not create unique index "users_email_key"
DETAIL: Key (email)=(...) is duplicated.
```

So this migration fails on the first run in production, but only *after* `ACCESS EXCLUSIVE` is acquired (see Blocker 2). Also, from `schema.sql`:

> "The nightly dedupe job (`bin/dedupe_users.py`) has not finished; **412 duplicate groups remain** as of 2026-11-16."

So the existing duplicates are known, and remediation is not finished.

### Blocker 2 — The constraint does not fix the bug the change is meant to fix

The motivating bug is a **check-then-act race** across 6 replicas:

> `accounts/forms.py:22` — `if User.objects.filter(email=email).exists(): raise ValidationError(...)` → "a `SELECT` followed by an `INSERT`: a classic check-then-act race; it does not prevent duplicates"

A `UNIQUE (email)` constraint (exact, case-sensitive) also fails to prevent the failure mode seen at login:

> `auth_backends.py:12` — `User.objects.get(email__iexact=email)` — "Two rows differing only in case would raise `MultipleObjectsReturned` at login."

Case-insensitive duplicates are more common than exact duplicates (1,437 and 1,205 groups, respectively). To match the login lookup, the constraint must be a **case-insensitive uniqueness rule**, e.g. `UNIQUE (lower(email))`, and application layer writes must stop normalizing only on `email.lower()` at the import layer (`import_customers.py:31`), not at the request layer.

### High — Locking risk on 2.1M rows

> `migration.sql`: `ALTER TABLE users ADD CONSTRAINT users_email_key UNIQUE (email);`

A plain `ADD CONSTRAINT ... UNIQUE` takes **`ACCESS EXCLUSIVE`** for the whole index build. That blocks all reads and writes on `users` while it scans ~2.1M rows, on a "high write traffic" table with 6 replicas. Even if duplicates did not exist, this is an outage-class change made in the migration transaction.

### Not searched

> "Any direct SQL from the analytics warehouse; not in this repository."

The warehouse may hold writes that bypass application normalization. Audit it before enforcing the constraint, because otherwise an `INSERT` that bypasses the constraint-free path could insert a case variant, or a constraint could reject legitimate low-level loads.

---

## Required path before merge

1. **Dedupe to zero, not "mostly."** The nightly script must finish and be re-verified:
   ```sql
   SELECT count(*) FROM (SELECT email FROM users GROUP BY email HAVING count(*) > 1) d;      -- must be 0
   SELECT count(*) FROM (SELECT email FROM users GROUP BY lower(email) HAVING count(*) > 1) d; -- must be 0
   ```
   Decide a merge policy for the 412 remaining groups: which row keeps the identity, and how dependent rows are repointed.

2. **Build the index concurrently, and never inside the migration transaction.** Django's `AddConstraint`/`unique=True` runs in `ATOMIC` and takes `ACCESS EXCLUSIVE`. The correct shape is:
   ```sql
   CREATE UNIQUE INDEX CONCURRENTLY users_email_lower_uidx ON users (lower(email));
   -- in a separate step, after the index is valid:
   ALTER TABLE users ADD CONSTRAINT users_email_lower_key UNIQUE USING INDEX users_email_lower_uidx;
   ```
   Use `AddIndexConcurrently` with `atomic = False`, wrapped to tolerate `CONCURRENTLY` failures (which leave an invalid index), then attach the constraint in a second, quick migration taking `ACCESS EXCLUSIVE` only briefly.

3. **Match the semantics to the code.** The constraint must be `lower(email)`. Either change the form to compare lowercase, or keep form comparison in sync; also raise the `iexact` lookup to an exact match on the normalized value.

4. **Backstop the race.** With the unique index in place, `create_user` must catch `IntegrityError` and return a validation error, and `import_customers.py` must use `ignore_conflicts=True` or an upsert, because `ignore_conflicts=False` will now fail the whole weekly import on one partner-provided duplicate.

5. **Audit the warehouse** for writes as noted above.

**Bottom line:** unsafe *now* (fails on existing duplicates, and does not fix the case-insensitive login bug), and unsafe *as written* (table-locking index build). Do not merge.