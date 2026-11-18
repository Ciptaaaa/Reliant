# Migration Guard Report — MY-007

## Verdict: **APPROVED**

Severity: **LOW** — informational/no action required.

## Summary

The migration adds a single nullable column at the end of `accounts` using `ALGORITHM=INSTANT`. On MySQL 8.0.36 this operation is genuinely supported as an instant, in-place metadata-only change. No rebuild, no table copy, no blocking.

## Evidence

**Operation type — append column, nullable, no non-null default:**
```sql
ALTER TABLE `accounts`
  ADD COLUMN `nickname` VARCHAR(80) DEFAULT NULL,
  ALGORITHM=INSTANT, LOCK=DEFAULT;
```
The column is appended at the end (`DEFAULT NULL`, no `NOT NULL`, no non-null default expression) — the instant-DDL-safe shape.

**Engine/version supports INSTANT for this change:**
- `context.md`: "**Engine**: MySQL 8.0.36, InnoDB"
- `schema.sql`: `ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 ...` — InnoDB, not partitioned (`-- Rows: ~2,400,000. No generated columns, no partitioned table.`)

MySQL 8.0 (8.0.12+) supports `ADD COLUMN ... ALGORITHM=INSTANT` for appended columns without a non-null default. 8.0.36 is well within range.

**INSTANT was actually applicable (not merely requested):**
- `context.md`: "The new column is at the **end** of the table and has no default value (`DEFAULT NULL`)."
- Table is non-partitioned, per `schema.sql` note.

Because the conditions for instant application hold, `ALGORITHM=INSTANT` will succeed rather than error/fall back. No rebuild occurs, so 2.4M rows are irrelevant to deploy time.

**No concurrent read/write risk:**
- `application-usage.md`: "**Existing references**: none. `grep -rn "nickname" app/ database/ resources/` returns no results."
- `context.md`: "No application code reads or writes `nickname` yet."
- Rolling deploy adds no lock contention since new code doesn't touch the column and old code is unaffected by an appended nullable column.

**Deploy ordering fine:**
- `context.md`: "The deploy runs migrations before starting new tasks." Adding a nullable, unused column ahead of any reader is safe.

## Findings

| # | Severity | Finding |
|---|----------|---------|
| 1 | LOW (info) | `ALGORITHM=INSTANT` is genuinely supported here: column appended at end, `DEFAULT NULL`, InnoDB on 8.0.36, non-partitioned table. No rebuild, no action needed. |

## Notes (non-blocking)

- The developer's claim ("adding a column at the end with INSTANT is supported on 8.0") checks out for 8.0.36. `LOCK=DEFAULT` is a no-op for an instant change.
- One client caveat mirrors the PG-007 pattern: instant-added columns require 8.0.29+ for the *server-side* default-value visibility to older clients, but since `nickname` is `DEFAULT NULL` and no reader exists yet, this is not triggered. The "Not searched: Mobile clients" note in `application-usage.md` is acceptable here because the API does not expose `nickname` until a later release.

## Conclusion

**APPROVED.** The migration is a metadata-only instant DDL that will not rebuild or block the `accounts` table (~2.4M rows) on MySQL 8.0.36. The only finding is LOW/informational confirming `INSTANT` is correctly applicable.