# Migration Guard Report — MY-007

## Summary
**Verdict: APPROVED** (findings severity: **LOW**)

The migration adds a nullable column at the end of the table with no non-null default. On MySQL 8.0.36 this ADD COLUMN is genuinely INSTANT-capable, so no table rebuild and no meaningful locking. The `ALGORITHM=INSTANT` request is both supported and actually applicable here.

---

## Findings

### [LOW] INSTANT is supported — but note the explicit rollback caveat
- **Evidence (engine/version):** `context.md` — "**Engine**: MySQL 8.0.36, InnoDB".
- **Evidence (column position):** `context.md` — "The new column is at the **end** of the table".
- **Evidence (default):** `migration.sql` — `` `nickname` VARCHAR(80) DEFAULT NULL `` and `context.md` — "has no default value (`DEFAULT NULL`)".
- **Evidence (no data/schema constraints):** `schema.sql` — "No generated columns, no partitioned table."
- **Assessment:** Adding a column that is last in order, nullable, with a NULL default qualifies for `ALGORITHM=INSTANT` on MySQL 8.0.12+. The explicit `ALGORITHM=INSTANT` means the ALTER will fail fast (rather than silently falling back to a table copy) if the engine cannot satisfy it — a good safety property.
- **Note:** Instant DDL in 8.0.29+ can only be *rolled back* (dropped) instantly if done before a subsequent non-instant schema change; plan to `DROP COLUMN ... ALGORITHM=INSTANT` if a revert is needed soon after. This is informational, not a blocker.

---

## Checks performed

| Check | Result | Evidence |
|---|---|---|
| Engine/version supports INSTANT ADD COLUMN | ✅ Pass | MySQL 8.0.36 (`context.md`) |
| Column added at end of table | ✅ Pass | `context.md` |
| Nullable / no non-null default | ✅ Pass | `DEFAULT NULL` (`migration.sql`) |
| Rebuild required? | ✅ No | Conditions above + "No generated columns, no partitioned table" (`schema.sql`) |
| Locking risk on 2.4M rows | ✅ Negligible | INSTANT path = metadata-only |
| App compatibility (rolling deploy) | ✅ Pass | "No application code reads or writes `nickname` yet" (`context.md`); `application-usage.md` — "Existing references: none" |
| Explicit ALGORITHM prevents silent copy | ✅ Good practice | `ALGORITHM=INSTANT` in `migration.sql` |

## Notes on the evidence supplied
- `application-usage.md` states mobile clients are "not affected: API does not expose `nickname` until the later release" — consistent with the column being unused at deploy time, so no read/write mismatch during the rolling deploy.
- Deploy ordering ("migrations before starting new tasks", `context.md`) is safe here because the new column is nullable and unused; a concurrent write path wouldn't break.

## Conclusion
No blocking issues. The requested `ALGORITHM=INSTANT` is supported **and** applicable. **APPROVED** with a single LOW/informational note about instant-rollback limitations.