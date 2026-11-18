# Migration Guard Report — MY-003

**Migration:** `database/migrations/2026_11_16_122000_customers_reference_unique.php` (raw SQL form)
**Table:** `customers` (~618,904 rows, InnoDB, MySQL 8.0.36)
**Verdict:** ❌ **BLOCK — do not merge.**

---

## 1. Summary of finding

The migration adds a `UNIQUE` index on `reference_code`, but the existing data already contains duplicates. The index build will fail with `ERROR 1062 (23000): Duplicate entry ... for key 'customers.customers_reference_code_unique'`, and because it is part of an `ALTER TABLE` with no `IGNORE`, nothing is applied — but the deploy still breaks (migration fails, rollout halts). The online-DDL clause does **not** help; it only addresses locking, not data validity.

---

## 2. Evidence

### 2.1 Duplicates exist (directly from `schema.sql`)

> ```sql
> --   SELECT COUNT(*) FROM (
> --     SELECT UPPER(reference_code) FROM customers
> --     GROUP BY UPPER(reference_code) HAVING COUNT(*) > 1
> --   ) d;                                                                             -> 3,067  -- case-insensitive duplicates
> ```

And explicitly called out:

> ```
> -- The collation utf8mb4_0900_ai_ci is case-insensitive, so the unique index will fail on 3,067
> -- groups, not 2,918.
> ```

Even the narrower, case-sensitive count is non-zero:

> ```
> --     SELECT reference_code FROM customers GROUP BY reference_code HAVING COUNT(*) > 1
> --   ) d;                                                                             -> 2,918  -- exact duplicates
> ```

**A `UNIQUE` index cannot be created over non-empty duplicate groups.** This is a hard blocker regardless of algorithm or lock mode.

### 2.2 Case sensitivity makes it worse, not better

The table collation is:

> ```
> ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;
> ```

`utf8mb4_0900_ai_ci` is case-insensitive and accent-insensitive ("ai_ci"). So `abc123` and `ABC123` are treated as **equal** for uniqueness purposes. The relevant count is therefore the **3,067** case-insensitive groups, not the 2,918 exact-duplicate groups — 149 additional groups of the form `abc123` vs `ABC123` will also collide.

### 2.3 A known source of lowercase data exists and was not addressed

From `application-usage.md`:

> `app/Console/Commands/ImportCustomers.php:44` — imports from the partner CSV; **preserves the case from the CSV**, which is sometimes lowercase.

> `app/Services/Customers/GenerateReference.php:18` — fixed generator, writes uppercase.

The generator was fixed, but the **import path still writes lowercase**. Mixed-case values are not a hypothetical — they are an active write path in production.

> - The partner CSV (external, regenerated weekly).

The CSV itself was **not searched** (explicitly listed under "Not searched"), so the full set of colliding values is unknown beyond the 3,067/2,918 counts.

### 2.4 The cleanup tool is stale and known-incomplete

From `context.md`:

> - There is a cleanup script (`artisan customers:dedupe`) that runs manually; the last run was three weeks ago and is known to have left some duplicates.

From `application-usage.md`:

> `app/Console/Commands/DedupeCustomers.php:27` — keeps the row with the lowest `id` per `reference_code` and deletes the rest. Requires a manual `--force`; **last run 2026-10-26, before the generator fix.**

Two problems with the dedupe tool as a mitigation:
- It groups by `reference_code` **raw value**, not `UPPER(reference_code)`. It will **not** merge `abc123` and `ABC123` because those strings differ byte-wise. It only removes exact duplicates.
- It was last run **before** the generator fix and pre-dates the current 3,067 figure; there is no evidence it was re-run after the last import.

### 2.5 `LOCK=DEFAULT` is not `LOCK=NONE` and does not guarantee online

The migration statement is:

```sql
ALTER TABLE `customers`
  ADD UNIQUE KEY `customers_reference_code_unique` (`reference_code`),
  ALGORITHM=INPLACE, LOCK=DEFAULT;
```

Notes:
- `LOCK=DEFAULT` merely means "use MySQL's default lock mode for this operation." For a **unique** secondary index build on MySQL 8.0, MySQL 8.0 can in fact do `INPLACE` / `LOCK=NONE` for adding a unique index **if the data allows it**, but the server is *not obligated* to grant `NONE`; if it cannot, it silently downgrades to `SHARED` or fails. Submitting `LOCK=DEFAULT` (rather than `LOCK=NONE`) hides that decision instead of forcing it, so the "online" claim is unverified by the statement itself.
- The developer's quoted statement in `context.md` used `LOCK=DEFAULT`, though context.md's narrative refers to `LOCK=NONE`. **These are not interchangeable.** `LOCK=NONE` is a request ("fail if you cannot do it without locking"); `LOCK=DEFAULT` is "whatever the server wants." For a reviewer, neither clause can rescue a failed unique build.
- Crucially: **lock mode is irrelevant to correctness**. The duplicate-key error happens during validation, before locking matters.

---

## 3. Impact on rolling deploy

- Laravel migrations run as a single transaction per migration on MySQL DDL (implicit commits). The `ALTER` fails; the migration row is not recorded; `artisan migrate` exits non-zero.
- In a rolling deploy, the instance running `migrate` fails its deploy step while other instances continue serving **without** the unique index. This leaves a partially-rolled environment and a re-run on the next attempt (which will fail identically) — a deploy loop.
- If someone "fixes" it by adding `IGNORE` (`ALTER IGNORE TABLE ...`), MySQL 8.0 **removed** `ALTER IGNORE` syntax; it will error. If they switch to `pt-online-schema-change` / `gh-ost`, those tools also abort on duplicate keys by default.

---

## 4. Required actions before this can merge

1. **Reduce duplicates to zero first**, using a value that matches the index's semantics — i.e. group by `UPPER(reference_code)` (or by the collation-aware expression), not raw `reference_code`:
   ```sql
   SELECT UPPER(reference_code), COUNT(*)
   FROM customers
   GROUP BY UPPER(reference_code)
   HAVING COUNT(*) > 1;   -- should return 0 rows before merging
   ```
2. **Reconcile the 3,067 case-insensitive groups** — decide which row survives per group. The existing `DedupeCustomers.php` must be changed (or a one-off script written) to group by `UPPER(reference_code)`, audit what was deleted (export survivors + victims), and be **re-run and re-verified** after the last import.
3. **Fix the import path** (`ImportCustomers.php:44`) to normalize case to uppercase on write (or `UPPER()` in the model accessor / column collation change). Otherwise the next weekly CSV re-introduces collisions and the unique index causes **import failures** instead of silent duplicates — arguably worse operationally.
4. **Verify the exact collation/expression of the index** matches what you dedupe against. Since the column is `utf8mb4_0900_ai_ci`, uniqueness is case- and accent-insensitive. If the intended business rule is "case-sensitive unique," you need `COLLATE utf8mb4_0900_as_cs` on the column (a separate, larger migration).
5. **Prove emptiness of the duplicate set** with the `UPPER`-based query above and attach the output to the ticket.
6. **Then** re-submit. At that point `ALGORITHM=INPLACE, LOCK=NONE` is the appropriate clause (use `LOCK=NONE`, not `DEFAULT`, so a lock downgrade fails loudly rather than silently blocking the table).

---

## 5. Minor notes (not blockers, but worth flagging)

- `LOCK=DEFAULT` should be `LOCK=NONE` for an explicit online-DDL intent; `DEFAULT` leaves the choice to the server and can silently take a `SHARED` lock on a 620k-row table.
- The generator fix and the import fix must land **before** the index, not after. Once the unique index exists, `ImportCustomers.php` preserving lowercase from the CSV will start throwing `1062` on import for any case-variant of an existing code.
- Apply the index in a maintenance window or via `gh-ost`/`pt-osc` with a duplicate-abort policy anyway, so a non-zero duplicate count aborts safely instead of partially migrating.

---

**Verdict: BLOCK.** The migration is syntactically valid and uses online-DDL clauses, but the data facts in `schema.sql` (2,918 exact / 3,067 case-insensitive duplicate groups) and the live lowercase write path in `ImportCustomers.php:44` mean the unique index **cannot** be built and, even if it could, would break the weekly partner import. Do not reassure on the `INPLACE`/`LOCK` clause — it addresses the wrong problem.