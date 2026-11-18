# Migration Guard Report — MY-007

## Migration under review
`database/migrations/2026_11_16_130000_accounts_add_nickname.php` (raw SQL form)

```sql
ALTER TABLE `accounts`
  ADD COLUMN `nickname` VARCHAR(80) DEFAULT NULL,
  ALGORITHM=INSTANT, LOCK=DEFAULT;
```

## Environment
- MySQL 8.0.36, InnoDB (`schema.sql`)
- Table `accounts`, ~2,400,000 rows (`schema.sql`: "Rows: ~2,400,000. No generated columns, no partitioned table.")
- Laravel 11, rolling deploy; migrations run before new tasks start (`context.md`)

## What the migration does
Adds one nullable column, `nickname VARCHAR(80) DEFAULT NULL`, at the end of `accounts`.

## Checks

| Check | Result | Evidence |
|---|---|---|
| Instant DDL actually applicable | Pass | `context.md`: "The new column is at the **end** of the table and has no default value (`DEFAULT NULL`)." Adding a trailing column with a NULL default is an instant-capable operation on 8.0.12+, and engine is 8.0.36. |
| Requested vs. real algorithm | Pass | `ALGORITHM=INSTANT` was requested **and** applies — no rebuild, metadata-only change. |
| Table has column at end / no positional insert | Pass | `AFTER` clause absent — column appended (`migration.sql`). |
| Default value forces rebuild | Pass | `DEFAULT NULL` is a nullable no-op default; no non-null default backfill. |
| Incompatible features (generated cols, partitioning, row format, fulltext) | Pass | `schema.sql`: "No generated columns, no partitioned table." |
| Existing app references to `nickname` | Pass (none) | `application-usage.md`: "grep -rn "nickname" app/ database/ resources/" returns no results." |
| Locking | Pass | `LOCK=DEFAULT` → for instant DDL, an exclusive metadata lock only for the brief DDL duration; no table rebuild. 2.4M rows not scanned. |
| Not-searched surface | Note | Mobile clients excluded by "API does not expose `nickname` until the later release" (`application-usage.md`). Consistent with "No application code reads or writes `nickname` yet" (`context.md`). |

## Findings

**LOW — `VARCHAR(80)` is a product decision pending in RFC 0051, not a migration hazard.**
The column length collation is inherited from the table (`utf8mb4_0900_ai_ci`, `schema.sql`). A later widening to e.g. `VARCHAR(255) inside a single-byte boundary` would still be instant in 8.0 (length byte change ≤255→>255 is instant), but if `display_name` is `VARCHAR(120)` and the fallback logic stores it into `nickname`, 80 chars could truncate. This is a data-semantics concern for the later release, not a blocker for this DDL. Recommend the RFC author confirm the 80-char limit is intentional (profile header truncation vs. store).

No `MEDIUM`/`HIGH`/`CRITICAL` findings.

## Notes on prior false-positive traps
- We did **not** claim a table rebuild: INSTANT was requested **and** is genuinely applicable (trailing column, NULL default, 8.0.36).
- We did **not** rely on version being below 8.0.12.
- The "zero-downtime" claim in `context.md` is accurate: instant ADD COLUMN holds only a momentary metadata lock, no row copy.

## Verdict

# APPROVED

Blocking findings: none. One `LOW` non-blocking note (confirm `VARCHAR(80)` is intentional vs. `display_name`'s 120-char field).