# Migration Guard Report — MY-007

## Verdict

**APPROVED** — single risk finding: **LOW**

The migration is safe to merge. `ALGORITHM=INSTANT` was requested *and* is actually applicable here, so the DDL will not rebuild the 2.4M-row table and will not block writes.

---

## Evidence reviewed

### 1. Engine and version support INSTANT column add

From `schema.sql`:
> `-- Current schema (MySQL 8.0.36, InnoDB)`

From `context.md`:
> `**Engine**: MySQL 8.0.36, InnoDB`

Instant DDL for `ADD COLUMN` exists since **8.0.12**; 8.0.36 qualifies. ✅

### 2. The column is appended at the end (required for INSTANT)

From `migration.sql`:
> `ADD COLUMN `nickname` VARCHAR(80) DEFAULT NULL,`

There is no `FIRST` / `AFTER` clause, so the column is added at the **end** of the table. From `context.md`:
> `The new column is at the **end** of the table`

Appending a column is exactly the case MySQL permits under INSTANT. ✅

### 3. No default that would force a rebuild

From `migration.sql`:
> `VARCHAR(80) DEFAULT NULL`

A **nullable** column with `DEFAULT NULL` does not require the "materialize implicit default" path. MySQL 8.0.13+ can even handle non-null defaults with INSTANT via a new row version, but here it is moot: `DEFAULT NULL` on a nullable column is the cheapest case. ✅

### 4. INSTANT was *requested* and is *applicable* (the distinction this case tests)

From `context.md`:
> `The column is at the end and has no non-null default, so `ALGORITHM=INSTANT` is genuinely supported on 8.0.12+.`
> `If the agent ignores the version or claims a rebuild, it is a false positive.`

Requested algorithm: `ALGORITHM=INSTANT` (in `migration.sql`).
Applicability checks:
- Version ≥ 8.0.12 → ✅ (8.0.36)
- Append-only (no positional clause) → ✅
- Nullable, `DEFAULT NULL` → ✅
- No generated column, no partitioning (from `schema.sql`: `No generated columns, no partitioned table`) → ✅
- Row format: table is default InnoDB; no `ROW_FORMAT=COMPACT/REDUNDANT` override → ✅

Conclusion: INSTANT applies. No table rebuild, no data copy. Consistent with `LOCK=DEFAULT` allowing concurrent DML.

### 5. Application safety for a rolling deploy

From `application-usage.md`:
> `**Existing references**: none. `grep -rn "nickname" app/ database/ resources/` returns no results.`
> `**Planned**: a later release will read `nickname` ... and fall back to `display_name` when it is `NULL``

From `context.md`:
> `No application code reads or writes `nickname` yet.`
> `The deploy runs migrations before starting new tasks.`

The column is nullable and unused by running code, so old and new tasks both operate correctly regardless of rollout order. Adding a nullable trailing column is backward compatible. ✅

### 6. Mobile clients

From `application-usage.md`:
> `Mobile clients (not affected: API does not expose `nickname` until the later release).`

Acknowledged; no client-visible impact since the API surface is unchanged in this release. ✅

---

## Findings

| # | Severity | Finding |
|---|----------|---------|
| 1 | **LOW** | `accounts` is large (~2.4M rows) but `ALGORITHM=INSTANT` is applicable (column appended at end, nullable, `DEFAULT NULL`, MySQL 8.0.36), so the operation is metadata-only. Residual risk is limited to the usual instant-DDL caveats below. |

No MEDIUM or HIGH findings.

---

## Notes / residual LOW-level caveats (non-blocking)

1. **Instant DDL history limit.** MySQL supports up to 64 instant-ALTER row versions per table; this consumes one. Not a problem at ~2.4M rows unless the table routinely gets instant DDL. Worth noting for change management, not a reason to block.
2. **Fallback behavior.** If for any reason INSTANT is rejected at deploy time (e.g., a future MySQL upgrade dropping support for instant on this table), the statement as written will **error out** rather than silently fall back — `ALGORITHM=INSTANT` is explicit. That is the desired fail-closed behavior; no `ALGORITHM=INPLACE` implicit fallback risk. (If a fallback were desired, the developer could drop the explicit `ALGORITHM` clause, but that would weaken the guarantee; keep it.)
3. **Read path is future work.** When the later release starts reading `nickname`, it must actually implement the `NULL` → `display_name` fallback described in `docs/rfc/0051-profile-nickname.md`. That is out of scope for this migration but is the one behavioral dependency to track.

---

## Recommendation

**Merge.** The migration achieves zero-downtime intent: `ALGORITHM=INSTANT` is supported on MySQL 8.0.36 for a trailing, nullable `DEFAULT NULL` column, so no table rebuild occurs; the column is unused by current code, making it neutral to the rolling deploy; and no client/API surface changes in this release. The single finding is LOW and does not warrant blocking.