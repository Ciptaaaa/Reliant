# Migration Guard Report — MY-006

**Statement reviewed:** `ALTER TABLE shipments ADD COLUMN status VARCHAR(20) NOT NULL DEFAULT 'pending', ALGORITHM=INPLACE, LOCK=SHARED;`

**Target:** MySQL 8.0.36, InnoDB, single primary + one async replica. Table `shipments`: ~6.5M rows, ~25 inserts/sec.

---

## Verdict: **HIGH RISK — do not merge as written**

This is *not* a "just adding a column" change. The explicit `ALGORITHM=INPLACE, LOCK=SHARED` is both stricter than required and interacts dangerously with an existing long-running transaction on the primary.

---

## Findings

### 1. The instant path is being overridden (avoidable cost)

> "On MySQL 8.0, adding a column at the end with a constant default can use `ALGORITHM=INSTANT`, in which case only `LOCK=DEFAULT` is permitted."

The `status` column is added at the end with a constant default (`'pending'`), which qualifies for INSTANT DDL on 8.0.36. By specifying `ALGORITHM=INPLACE, LOCK=SHARED`, the developer **forces a full table rebuild** of ~6.5M rows instead of an O(1) metadata-only change. This is a self-inflicted regression.

> "the developer's explicit `INPLACE`/`LOCK=SHARED` is *more* restrictive than necessary"

### 2. `LOCK=SHARED` blocks all writes for the duration of the rebuild

`LOCK=SHARED` on an `INPLACE` rebuild permits reads but **blocks writes**. During a 6.5M-row rebuild, every `INSERT` from `CreateShipment.php:29` (and the older deployed code) stalls:

> "`app/Services/Shipping/CreateShipment.php:29` — inserts new shipments with `status` (the new code)."
> "Inserts: ~25/second."

That is a write outage for the entire rebuild window.

### 3. Metadata-lock pile-up behind the settlement transaction (the critical risk)

> "`app/Console/Commands/SettlementReport.php:58` … `lockForShare()` … 2–6 minutes … **on the primary** … It holds a shared metadata lock on `shipments` for the whole 2–6 minutes."

> "MySQL metadata locks are held for the duration of a transaction; a queued `ALTER` waiting for an MDL serialises later requests on the same table, which is the classic MDL pile-up."

Sequence of failure:
1. Settlement report opens a transaction and takes a **shared MDL** on `shipments` for 2–6 min.
2. The `ALTER` requests an **exclusive MDL** and queues behind it.
3. Because the `ALTER` is queued, every subsequent query on `shipments`—including the hot path—**queues behind the `ALTER`**.
4. > "`app/Http/Controllers/ShipmentTrackingController.php:19` — `SELECT … WHERE id = ?` … the highest-QPS query on the table."

Result: the highest-QPS endpoint on the table can stall for up to 6 minutes (MDL wait timeout default is 31536000s, so it will *not* self-abort; `max_execution_time`/`lock_wait_timeout` are the only safety nets). This is a production outage, not a slow migration. The 15-minute cadence means the ALTER only needs to catch *one* running settlement window to trigger this.

Additionally, the queued `ALTER` can itself be blocked, then the settlement report's next run queues behind it — a cascading pile-up.

### 4. Replica lag

> "one async replica"

The rebuild's binlog volume (row images for a 6.5M-row change / DDL propagation) will cause replica lag. Not in scope of the primary outage, but worth flagging. The analytics replica job was "not searched" and runs on the replica, so it should be independently verified, but it is not a primary-side blocker.

---

## Evidence summary

| Evidence | Source | Implication |
|---|---|---|
| Constant default + appended column | `migration.sql` | Qualifies for `ALGORITHM=INSTANT` |
| `ALGORITHM=INPLACE, LOCK=SHARED` | `migration.sql` | Forces rebuild + blocks writes |
| 2–6 min shared MDL every 15 min on primary | `application-usage.md:58` | ALTER will queue behind it |
| Highest-QPS SELECT on `shipments` | `application-usage.md:19` | Queued queries pile up behind ALTER |
| ~25 inserts/sec | `schema.sql` | Write outage during rebuild |
| 8.0.36 | `context.md` | INSTANT is available; verify on staging |

---

## Required changes before merge

1. **Drop the explicit algorithm/lock hints.** The correct statement is:
   ```sql
   ALTER TABLE `shipments`
     ADD COLUMN `status` VARCHAR(20) NOT NULL DEFAULT 'pending';
   ```
   On 8.0.36 this should resolve to `ALGORITHM=INSTANT`. **Verify on staging** with:
   ```sql
   ALTER TABLE shipments ADD COLUMN status VARCHAR(20) NOT NULL DEFAULT 'pending', ALGORITHM=INSTANT;
   ```
   If it fails (e.g., because of a pre-existing column position constraint or a future 8.0 minor regression), do **not** silently fall back to `INPLACE`; escalate.

2. **If for any reason a non-instant path is unavoidable**, schedule it in a maintenance window and apply a lock-wait guard on the session before running:
   ```sql
   SET SESSION lock_wait_timeout = 5;
   ```
   plus an outer retry loop. This ensures the ALTER fails fast rather than queueing behind the 2–6 min settlement MDL and blocking the hot path.

3. **Verify the actual ALGORITHM chosen** by reading `performance_schema.events_statements_history` / `SHOW WARNINGS` after the statement, or by checking `INFORMATION_SCHEMA.INNODB_TABLES.INSTANT_COLS`. Do not rely on the intention.

4. **Move `SettlementReport.php` off the primary** or shorten its `lockForShare()` scope. This is a separate ticket but is the root cause of the MDL exposure; leaving it means *any* future DDL on `shipments` carries the same pile-up risk.

5. **Confirm the analytics replica job** is unaffected — it was "not searched" and runs on the replica.

---

## Classification

`HIGH` — not `LOW`. The change as written:
- Forces an unnecessary full-table rebuild of 6.5M rows (self-inflicted, avoidable via INSTANT),
- Sets `LOCK=SHARED`, which blocks writes for the rebuild duration,
- And, most critically, will queue behind (and then cause a pile-up behind) the 2–6 minute settlement transaction, stalling the table's highest-QPS read path.

The correct fix is a one-line simplification, but it must be verified, not assumed.