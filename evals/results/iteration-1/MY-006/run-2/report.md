# Migration Guard Report — MY-006

**Migration:** `database/migrations/2026_11_16_125000_shipments_add_status.php`
```sql
ALTER TABLE `shipments`
  ADD COLUMN `status` VARCHAR(20) NOT NULL DEFAULT 'pending',
  ALGORITHM=INPLACE, LOCK=SHARED;
```

**Table:** `shipments` — BIGINT PK, ~6,500,000 rows, ~25 inserts/s, InnoDB, MySQL 8.0.36
**Verdict: 🔴 HIGH RISK — DO NOT MERGE AS-IS**

---

## Summary

This is *not* "just adding a column." The explicit `ALGORITHM=INPLACE, LOCK=SHARED` clause **defeats the most favorable execution path** (INSTANT) and **forces a full table rebuild** while taking a shared lock that blocks all writes. Worse, the lock acquisition must queue behind an existing long-running transaction on the primary, which — per the documented meta-lock semantics — funnels *every* subsequent query on `shipments` behind the waiting `ALTER`. The result is a multi-minute write outage on the hottest-read table, with an unbounded MDL pile-up.

---

## Evidence

**1. The explicit algorithm forces a rebuild instead of an instant metadata-only change.**

From `context.md`:
> "On MySQL 8.0, adding a column at the end with a constant default can use `ALGORITHM=INSTANT`, in which case only `LOCK=DEFAULT` is permitted. The developer has explicitly asked for `INPLACE` + `LOCK=SHARED`, which forces a rebuild..."

The column is added at the end with a constant default (`'pending'`) — exactly the instantly-addable shape. `INSTANT` would touch only the data dictionary. Instead, `INPLACE` re-copies all ~6.5M rows.

**2. A 2–6 minute MDL is held on the primary every 15 minutes — right at the table this ALTER targets.**

From `application-usage.md`:
> "It holds a shared metadata lock on `shipments` for the whole 2–6 minutes."
> "Runs every 15 minutes **on the primary** (no read replica routing configured for this command)."

The settlement transaction's `lockForShare()` on `shipments` acquires an `MDL_SHARED_READ`. Our `ALTER TABLE` needs an `MDL_SHARED_WRITE` (blocking new writers) or stronger; it cannot get it while that transaction holds its shared lock.

**3. The blocked ALTER serialises everything behind it — the MDL pile-up.**

From `context.md`:
> "MySQL metadata locks are held for the duration of a transaction; a queued `ALTER` waiting for an MDL serialises later requests on the same table, which is the classic MDL pile-up."

And from `application-usage.md`:
> "`SELECT … WHERE id = ?` per request. In production this is the highest-QPS query on the table."

With `LOCK=SHARED` the migration is queued first; every new `shipments` query — including that highest-QPS single-row lookup — queues behind the un-granted ALTER. If the settlement job starts within the 2–6 minute window, the ALTER waits, and the write/read pile-up grows for minutes. Under load this can cascade to application-wide timeouts, not just on `shipments`.

**4. Lock request timeout risk will abort mid-rebuild.**

MySQL's `lock_wait_timeout` (default 31536000s for DDL, but often tuned down) combined with a 2–6 minute MDL means the ALTER may burn the entire rebuild and then fail acquiring the exclusive phase, leaving the schema unchanged but the pile-up having already occurred.

**5. Replica impact (lower severity, still note).**

From `context.md`: "single primary, one async replica." A rebuild generated on the primary executes the same DDL on the replica in the binlog stream. Adding latency there is a lag risk, though the analytics job is not on the primary.

---

## Risk classification

| Factor | Assessment |
|---|---|
| Algorithm | INPLACE → full rebuild of ~6.5M rows (avoidable; INSTANT applies) |
| Locking | `LOCK=SHARED` blocks all writes for rebuild duration |
| Interaction with existing workload | Waits behind 2–6 min MDL every 15 min; piles up subsequent queries |
| Hot path impact | Highest-QPS `WHERE id = ?` query affected |
| Write throughput | ~25 inserts/s blocked; older deployed code inserting without `status` must be covered by the default (it is) |

This is **HIGH**, not LOW. "Adding a column" is true only in name; the chosen clauses turn it into a blocking table rebuild with a metadata-lock amplification effect on the primary.

---

## Required changes before merge

**Option A — use the instant path (preferred):**

```sql
ALTER TABLE `shipments`
  ADD COLUMN `status` VARCHAR(20) NOT NULL DEFAULT 'pending',
  ALGORITHM=INSTANT;
```

Verify on a staging copy of the 8.0.36 instance that `SHOW WARNINGS` / `performance_schema` reports the INSTANT path (i.e., operates in place, metadata only, no rebuild). For 8.0.12+ this shape is instant-addable. Do **not** specify `LOCK=SHARED` with `INSTANT` — per the context, only `LOCK=DEFAULT` is permitted.

**Option B — if for some reason a rebuild is unavoidable, decouple locks:**

```sql
SET SESSION lock_wait_timeout = 5;
ALTER TABLE `shipments`
  ADD COLUMN `status` VARCHAR(20) NOT NULL DEFAULT 'pending',
  ALGORITHM=INPLACE, LOCK=NONE;
```
- Use `LOCK=NONE` (online) rather than `LOCK=SHARED`, so reads and writes proceed during the rebuild — available for simple additive column changes.
- Set a short `lock_wait_timeout` and retry on failure, so a single wait does not queue indefinitely behind the settlement job.
- Schedule/maintenance-window coordination with the settlement job (see below).

**Also required regardless of option:**
1. **Move the settlement job off the primary** or stop it from holding the MDL for its scan — read it from the replica, and/or pull the aggregate outside a write transaction. The `DB::transaction(function(){ …lockForShare()… })` holding a shared MDL for 2–6 minutes is a standing infrastructure hazard independent of this migration.
2. **Verify the default covers legacy writers.** `application-usage.md` says older code inserts without `status`; the `DEFAULT 'pending'` covers it — confirm in the read path that no application treats the column's absence as special.
3. Add a **backfill/verification step** if the intended semantic differs from `'pending'` for historical rows (recall: existing 6.5M rows get the default literal — check that is the intended meaning for already-shipped records).

---

## Verdict

**HIGH — reject as written.** The `ALGORITHM=INPLACE` + `LOCK=SHARED` combination converts an instantly-completable schema change into a blocking rebuild, and — because it must acquire its metadata lock on a primary that periodically holds a 2–6 minute shared MDL on the same table — it will pile the application's highest-QPS query behind the waiting DDL. Re-submit with `ALGORITHM=INSTANT` (verified against the actual 8.0.36 instance), or, if a rebuild is truly needed, `LOCK=NONE` with a short lock-wait timeout, and separately move the settlement transaction's lock off the primary. Do not merge before these changes and verification on a replica of production scale.
