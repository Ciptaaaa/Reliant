# Migration Guard Report — MY-007

**Migration:** `database/migrations/2026_11_16_130000_accounts_add_nickname.php` (raw SQL form)
**Target:** MySQL 8.0.36, InnoDB, `accounts` (~2.4M rows)

---

## Findings

### [LOW] `ALGORITHM=INSTANT` requested and genuinely applicable — informational only

**Evidence — the migration:**

```sql
ALTER TABLE `accounts`
  ADD COLUMN `nickname` VARCHAR(80) DEFAULT NULL,
  ALGORITHM=INSTANT, LOCK=DEFAULT;
```

**Evidence — it is being applied to a usable instant-DDL path:**
- Engine/version: `MySQL 8.0.36, InnoDB` (context.md). Instant ADD COLUMN is supported from 8.0.12 for columns added **at the end** of the table.
- Deployment facts: "The new column is at the **end** of the table and has no default value (`DEFAULT NULL`)."
- `VARCHAR(80) DEFAULT NULL` → nullable, no non-null default expression. It does not hit the known `INSTANT` restrictions (no non-null default, not a generated/stored column, not a positional insert in the middle of the table).

**Conclusion:** `ALGORITHM=INSTANT` is *actually* applicable here, not merely requested. On 8.0.36 this is a metadata-only change (InnoDB instant ADD COLUMN v1), so it will not rebuild the table or lock it, regardless of the 2.4M row count.

**Note (optional, not blocking):** If you want to fail fast rather than silently fall back, you may consider running with `ALGORITHM=INSTANT` alone (no `LOCK=DEFAULT`) — but that is already the case here, so no change is required. Preserve a `LOCK=NONE`/`ALGORITHM=INSTANT` guard in CI as a general policy.

---

## Application-usage check

**Evidence — application-usage.md:**

> - **Existing references**: none. `grep -rn "nickname" app/ database/ resources/` returns no results.
> - **Planned**: a later release will read `nickname` in the profile header and fall back to `display_name` when it is `NULL`

**Evidence — context.md:**

> - No application code reads or writes `nickname` yet.
> - The deploy runs migrations before starting new tasks.

**Assessment:** Adding a nullable, default-less column with zero current readers/writers is backward-compatible for the rolling deploy: old tasks never touch it, new tasks don't yet depend on it, and the later release's `NULL → display_name` fallback is consistent with the column being nullable.

**Scope caveat (informational):** The search scope in application-usage.md is limited to `app/ database/ resources/`, and it explicitly declares mobile clients out of scope ("not affected: API does not expose `nickname` until the later release"). That reasoning holds for this migration only because nothing reads or writes the column yet; it should be re-verified before the later release that actually exposes `nickname` over the API.

---

## Verdict

**APPROVED**

- The single finding is **LOW**: `ALGORITHM=INSTANT` was requested **and** is genuinely applicable on MySQL 8.0.36 for a trailing, nullable, no-default column.
- No table rebuild, no data copy, no blocking lock, and no application references — safe for the rolling deploy and the 2.4M-row table.