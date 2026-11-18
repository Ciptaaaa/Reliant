# Migration Guard Report — MY-006

## Verdict: **HIGH RISK — DO NOT MERGE AS-IS**

The migration as written forces a table rebuild and will block writes, while also getting stuck behind a long-running metadata lock (MDL) holder on the primary. The "it's just adding a column" framing is wrong on MySQL 8.0 for this exact statement. It must be changed to an instant add (preferred) or guarded with an MDL-safe strategy before merge.

---

## Migration under review

```sql
ALTER TABLE `shipments`
  ADD COLUMN `status` VARCHAR(20) NOT NULL DEFAULT 'pending',
  ALGORITHM=INPLACE, LOCK=SHARED;
```

---

## Risk 1 — Explicit `INPLACE`/`LOCK=SHARED` defeats the INSTANT path (table rebuild)

Evidence (`context.md`):

> "On MySQL 8.0, adding a column at the end with a constant default can use `ALGORITHM=INSTANT`, in which
> case only `LOCK=DEFAULT` is permitted. The developer has explicitly asked for `INPLACE` + `LOCK=SHARED`,
> which forces a rebuild and blocks writes for the duration instead of taking the instant path."

- The column is appended at the end with a constant default (`'pending'`), which is precisely the shape that qualifies for `ALGORITHM=INSTANT` on 8.0.
- `ALGORITHM=INPLACE` for `ADD COLUMN` is an in-place **rebuild** (copy + rebuild of the table), not a metadata-only operation.
- `LOCK=SHARED` permits reads during the rebuild but **blocks all writes** to `shipments` for the full duration.
- On ~6.5M rows (`schema.sql`) with ~25 inserts/s (`schema.sql`), the rebuild is minutes-scale, and write traffic is blocked throughout.

Effective cost: seconds of work (metdata only) inflated to a multi-minute read-only window.

## Risk 2 — MDL pile-up behind the settlement report (availability outage)

Evidence (`application-usage.md`):

> "`app/Console/Commands/SettlementReport.php:58`
> ```php
> DB::transaction(function () {
>     DB::table('shipments')->where('shipped_at', '<', $cutoff)->lockForShare()->get();
>     // … 2–6 minutes of aggregation in PHP …
> });
> ```
> Runs every 15 minutes **on the primary** (no read replica routing configured for this command). It
> holds a shared metadata lock on `shipments` for the whole 2–6 minutes."

Evidence (`context.md`):

> "MySQL metadata locks are held for the duration of a transaction; a queued `ALTER` waiting for an MDL
> serialises later requests on the same table, which is the classic MDL pile-up."

This is the dangerous part. The `ALTER` must obtain an exclusive metadata lock before it can proceed. If it arrives while `SettlementReport` holds the shared MDL (2–6 minutes, every 15 minutes):

1. The `ALTER` waits for the settlement transaction's shared MDL.
2. While waiting, the `ALTER` sits at the head of the MDL queue and requests an exclusive lock.
3. Every new query on `shipments` then queues **behind the ALTER** for its own MDL (the pile-up).
4. The highest-QPS query on the table is `SELECT … WHERE id = ?` per request (`application-usage.md`, `ShipmentTrackingController.php:19`), so the pile-up directly takes out the tracking endpoint.
5. The realistic outcome is connection exhaustion and a full table-level outage, not just a slow migration.

Writes from `CreateShipment.php:29` and older deployed code (`application-usage.md`) are blocked by `LOCK=SHARED` too, so failed inserts stack up as well.

## Risk 3 — Default-backfill compatibility check

Evidence (`application-usage.md`):

> "Older deployed code inserts without `status`; the new default covers it."

The `NOT NULL DEFAULT 'pending'` is compatible with old writers, and with `INSTANT` on 8.0 the existing rows get the default in metadata (no row rewrite). That is fine, provided the instant path is used. Under the current `INPLACE` rebuild, the existing 6.5M rows are physically rewritten with the default, and that is a large operational cost for no benefit.

## Risk 4 — Not searched

Evidence:

> "Not searched: The analytics replica job (runs on the replica, not the primary)."

The replica job is excluded from the blocking analysis. It does not change the primary-side verdict, but it should be noted that replica lag and replica-side schema application still need to be handled by the deployment process, since the DDL still replicates.

---

## What must change before merge

1. **Use INSTANT (preferred).** Change to:
   ```sql
   ALTER TABLE `shipments`
     ADD COLUMN `status` VARCHAR(20) NOT NULL DEFAULT 'pending',
     ALGORITHM=INSTANT;
   ```
   - **Verify on the actual target version.** The statement must be allowed with `ALGORITHM=INSTANT` and succeed in a pre-merge, non-production check on the real MySQL build/version. Do not merge on assumption.
   - If any condition prevents INSTANT (e.g. instant column-count/row-size limits already reached, unusual prior DDL), fall back is a **deliberate, scheduled rebuild with rollback plan**, not the current statement swapped in blind.

2. **Do not merge the `INPLACE` + `LOCK=SHARED` form.** Even if instant is somehow not available, run during a change window, take all traffic off or drain the settlement report, and set explicit `lock_wait_timeout` for the DDL session plus a retry/abort strategy. Never let the ALTER sit indefinitely in the MDL queue behind the settlement transaction.

3. **Coordinate with the settlement report.** Because it runs every 15 minutes on the primary and holds an MDL for 2–6 minutes:
   - Pause or point it at the replica for the change window if instant is not used.
   - For the INSTANT path, the MDL hold is brief, but still verify there is no concurrent 6-minute settlement transaction starting at the same moment (a short lock timeout on the DDL session with retry is cheap insurance).

4. **Monitoring.** Watch `performance_schema.metadata_locks` and `SHOW PROCESSLIST` during deploy for an ALTER waiting on MDL and stacked query waits.

## Summary

| Risk | Severity | Evidence |
|---|---|---|
| `LOCK=SHARED` + `INPLACE` forces rebuild, blocks writes | High | `context.md`, `schema.sql` |
| MDL pile-up behind `SettlementReport` on primary | High (outage) | `application-usage.md`, `context.md` |
| 6.5M-row physical rewrite for a constant default | Medium | `schema.sql`, `application-usage.md` |
| Compatible for old writers (default) | OK | `application-usage.md` |

**Verdict: do not merge. Replace with `ALGORITHM=INSTANT` after verifying it is allowed on the target MySQL 8.0 build, then merge with the change window and settlement-report coordination noted above.**