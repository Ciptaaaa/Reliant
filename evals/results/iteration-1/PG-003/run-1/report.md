# Migration Guard Report — PG-003

**Verdict: BLOCKER — do not merge as written.**

The migration as submitted will fail outright on the current data, and the statement form is also unsafe for the write traffic even after the data is cleaned. Both issues must be addressed before merge.

---

## Finding 1 — BLOCKER: `ADD CONSTRAINT ... UNIQUE` will fail on existing duplicate rows

**Evidence (schema.sql, dev-run counts on 2026-11-16):**

> `SELECT count(*) FROM users;` -> 2,104,882
> `SELECT count(*) FROM (SELECT email FROM users GROUP BY email HAVING count(*) > 1) d;` -> **1,205** — exact duplicates
> `SELECT count(*) FROM (SELECT email FROM users GROUP BY lower(email) HAVING count(*) > 1) d;` -> **1,437** — case-insensitive duplicates

PostgreSQL builds the unique index while `ALTER TABLE ... ADD CONSTRAINT` executes. Any duplicate key causes `ERROR: could not create unique index "users_email_key" ... DETAIL: Key (email)=(...) is duplicated.` The migration aborts, and on PostgreSQL the `ALTER` is atomic — but the deploy pipeline will still surface a failed migration, and whatever runs it (Django `migrate` in a rollout) will leave the migration in a failed/unapplied state.

The developer's claim *"The app already validates emails in the form, so there should be no duplicates"* is contradicted directly by the pasted counts. Form validation is not a data fact.

**Evidence (application-usage.md) that duplicates are expected and ongoing:**

> `accounts/forms.py:22` — a `SELECT` followed by an `INSERT`: a classic check-then-act race; it does not prevent duplicates under concurrency across 6 replicas.

And:

> The nightly dedupe job (`bin/dedupe_users.py`) has not finished; **412 duplicate groups remain** as of 2026-11-16.

**Required before this migration can exist at all:** the exact-duplicate set (1,205 groups) must be resolved to zero *by exact email*. Case-insensitive duplicates (1,437 groups) are a separate problem — see Finding 3.

---

## Finding 2 — HIGH: lock held is `ACCESS EXCLUSIVE`, unsafe on a hot 2.1M-row table

**Evidence (context.md):**

> even without duplicates an unqualified `ADD CONSTRAINT UNIQUE` takes `ACCESS EXCLUSIVE` while it builds the index on 2.1M rows

**Evidence (context.md deployment facts):**

> Table: `users`, ~2.1M rows, **high write traffic**.
> The API is a monolith with **6 replicas** behind a load balancer.

`ALTER TABLE users ADD CONSTRAINT users_email_key UNIQUE (email)` acquires `ACCESS EXCLUSIVE`. During the index build every `SELECT`, `INSERT`, `UPDATE`, and `DELETE` on `users` blocks. On 2.1M rows that is seconds-to-minutes of full-table unavailability across all 6 replicas, and the queue of blocked writers can compound it. This is unacceptable regardless of whether the data is clean.

**Correct pattern** — build the index concurrently, then attach it:

```sql
-- Step 1: build the index without blocking writes (runs outside a transaction)
CREATE UNIQUE INDEX CONCURRENTLY users_email_key_idx ON users (email);

-- Step 2: attach as a constraint using the existing index (takes ACCESS EXCLUSIVE briefly,
-- but does not rebuild the index — milliseconds)
ALTER TABLE users
  ADD CONSTRAINT users_email_key UNIQUE USING INDEX users_email_key_idx;
```

Operational notes for Step 1: `CREATE INDEX CONCURRENTLY` cannot run inside a transaction block, so the migration framework must be told to run it in autocommit (Django: `atomic = False` on the migration class, or run it via a raw autocommit connection). If it fails mid-build it leaves an INVALID index that must be `DROP INDEX`ed before retry — Step 1 should be idempotent/retried by a human or a guarded script, not blindly by `migrate`.

---

## Finding 3 — MEDIUM (design/correctness): case sensitivity mismatch will not fix the login bug

**Evidence (application-usage.md):**

> `auth_backends.py:12` — `User.objects.get(email__iexact=email)` — login lookup is case-insensitive. **Two rows differing only in case would raise `MultipleObjectsReturned` at login.**

> `accounts/management/commands/import_customers.py:31` — `bulk_create` ... **casing is normalised only on `email.lower()`**.

A `UNIQUE (email)` constraint is **case-sensitive**. After this migration succeeds:

- `Foo@x.com` and `foo@x.com` can still coexist → `MultipleObjectsReturned` at login is still possible.
- The weekly CSV import, which only lowercases, can still insert a second casing of an existing address.

If the intent is to make the login lookup unambiguous, the constraint must match the lookup:

```sql
CREATE UNIQUE INDEX CONCURRENTLY users_email_lower_key_idx ON users (lower(email));
ALTER TABLE users ADD CONSTRAINT users_email_lower_key UNIQUE USING INDEX users_email_lower_key_idx;
```

(This must be built only after the 1,437 case-insensitive groups are resolved, not just the 1,205 exact ones.) This changes the schema design, so it needs a decision rather than a drop-in, but the current migration as written does **not** achieve the stated goal of preventing the `MultipleObjectsReturned` failure.

---

## Finding 4 — LOW: application-layer check should remain but is not a guarantee

**Evidence (application-usage.md):**

> a `SELECT` followed by an `INSERT`: a classic check-then-act race; **it does not prevent duplicates under concurrency across 6 replicas.**

The `forms.py` check is a UX affordance (nice error message) and should stay. It is not a substitute for the DB constraint, and it is not evidence that the constraint is safe to add. Keep it; just don't rely on it.

---

## Required changes before merge

1. **Clean the data first.** Resolve all exact duplicates (1,205 groups / 412 reported still open). Verify with:
   ```sql
   SELECT count(*) FROM (SELECT email FROM users GROUP BY email HAVING count(*) > 1) d; -- must be 0
   ```
   Confirm again on production immediately before deploy — the dev counts are from 2026-11-16 and writes continue.
2. **Replace the migration** with the `CREATE UNIQUE INDEX CONCURRENTLY` + `ADD CONSTRAINT ... USING INDEX` pair (Finding 2), managed outside a transaction block.
3. **Decide on case sensitivity.** If the `__iexact` login bug is in scope, use a `lower(email)` unique index (Finding 3) instead, and resolve the 1,437 case-insensitive groups first.
4. **Do not merge #1–3 as a single `migrate` in the rollout** if the index build window is long — coordinate so the concurrent index build completes before the constraint attach runs.

Until items 1 and 2 are done, this migration is **unsafe to merge and unsafe to deploy**.