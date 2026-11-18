# Migration Guard Report — MY-006

**Verdict: HIGH RISK — do not merge as written.**

The migration as written is not a safe "just adding a column" change. It is a table rewrite that will pile up on the primary behind an already-observed 2–6 minute metadata lock holder.

---

## 1. What the migration actually does

```sql
ALTER TABLE `shipments`
  ADD COLUMN `status` VARCHAR(20) NOT NULL DEFAULT 'pending',
  ALGORITHM=INPLACE, LOCK=SHARED;
```

The intent — `ADD COLUMN ... NOT NULL DEFAULT 'pending'` — is real and the application depends on it:

> `app/Services/Shipping/CreateShipment.php:29` — inserts new shipments with `status` (the new code). Older deployed code inserts without `status`; the new default covers it.

But the **explicit algorithm and lock clause** override the fast path:

> "On MySQL 8.0, adding a column at the end with a constant default can use `ALGORITHM=INSTANT`, in which
> case only `LOCK=DEFAULT` is permitted. The developer has explicitly asked for `INPLACE` + `LOCK=SHARED`,
> which forces a rebuild and blocks writes for the duration instead of taking the instant path." (`context.md`)

So this is a **full table rebuild of ~6.5M rows** with `LOCK=SHARED`, not an instant metadata change.

---

## 2. MDL pile-up on the primary

The critical fact:

> "`app/Console/Commands/SettlementReport.php:58` … `DB::transaction(... lockForShare()->get(); // … 2–6 minutes of aggregation in PHP …)`"
>
> "Runs every 15 minutes **on the primary** … It holds a shared metadata lock on `shipments` for the whole 2–6 minutes." (`application-usage.md`)

> "MySQL metadata locks are held for the duration of a transaction; a queued `ALTER` waiting for an MDL
> serialises later requests on the same table, which is the classic MDL pile-up." (`context.md`)

The exact failure sequence:

1. The `ALTER` arrives while the settlement `SELECT ... LOCK IN SHARE MODE` is active. The `ALTER` needs an **exclusive** MDL to rebuild, so it **queues** behind the shared MDL.
2. Every subsequent query on `shipments` — including:
   > "`app/Http/Controllers/ShipmentTrackingController.php:19` — `SELECT … WHERE id = ?` per request. In production this is the highest-QPS query on the table." (`application-usage.md`)
   — queues **behind the ALTER** (MySQL queued-MDL fairness).
3. Every new write — in production at **"Inserts: ~25/second"** (`schema.sql`) — also queues.
4. Each of those queued requests holds its own open transaction/connection, consuming `max_connections` and lock-wait budget. If the settlement transaction takes the full 6 minutes, that's ~25/s × 360s ≈ **9,000 writes plus all highest-QPS reads** stalled behind one ALTER.

The result is not a slow migration — it is a **read/write outage on the shipment tracking path** for the duration of the settlement transaction, plus possible connection-pool exhaustion.

---

## 3. Secondary risks

- **Replication lag.** `context.md`: "single primary, one async replica." A 6.5M-row rebuild serialized to the binlog will apply on the replica and can cause significant lag; any replica readers following the replicas will see stale tracking data.
- **`LOCK=SHARED` still blocks writes.** Even outside the settlement window, `LOCK=SHARED` permits concurrent reads but blocks concurrent DML/DDL for the full rebuild. "Blocks writes" is explicitly a property of the chosen options, not a side effect.
- **The developer's stated rationale is wrong.** "It is just adding a column, so it should be fast" (`context.md`) is only true for the `INSTANT` path, which the explicit `ALGORITHM=INPLACE, LOCK=SHARED` prevents.

---

## 4. Required remediation

Merge is blocked until **all** of the following are satisfied:

1. **Use the instant path and verify it on the actual server version.** On 8.0.36, appending a `NOT NULL` column with a constant default qualifies for `ALGORITHM=INSTANT`. Confirm with a staging dry-run:
   ```sql
   ALTER TABLE `shipments`
     ADD COLUMN `status` VARCHAR(20) NOT NULL DEFAULT 'pending',
     ALGORITHM=INSTANT, LOCK=DEFAULT;
   ```
   Do not force `INPLACE` / `LOCK=SHARED` when `INSTANT` is available and admissible ("only `LOCK=DEFAULT` is permitted" on that path).
2. **If `INSTANT` cannot be verified**, do not run the rebuild on the primary. At minimum:
   - run during a window where the settlement transaction is guaranteed not to overlap, or
   - temporarily route the settlement report off the primary (the read-replica lag used for its aggregation is the correct target; `application-usage.md` notes it is not yet configured — fix that first), or
   - use a managed online-schema-change tool (e.g., `pt-online-schema-change` / `gh-ost`) with a lock timeout and copy chunks.
3. **Guard against MDL pile-up regardless of path.** Wrap the DDL with an explicit submission approach that does not outlast `lock_wait_timeout` on the table traffic, and add a runbook step to abort if the settlement job is present:
   ```sql
   SET SESSION lock_wait_timeout = 5;
   ```
   with retry on timeout; abort if `performance_schema.metadata_locks` shows an active `SHARED` lock on `shipments` from `SettlementReport`.
4. **Check in-flight transactions before DDL.** Confirm `information_schema.innodb_trx` is empty for `shipments` just before running the statement.

---

## 5. Risk classification

**HIGH.** Reasons:

- Explicit table rebuild (`INPLACE`, `LOCK=SHARED`) on a 6.5M-row, ~25 inserts/s primary.
- Interacts with a known 2–6 minute metadata-lock holder on the primary running every 15 minutes.
- Blocks the highest-QPS query on the table (`ShipmentTrackingController.php:19`).
- Known and named MDL pile-up mechanism (`context.md`).
- Replication-lag exposure on the single async replica.

The size of the change ("just a column with a default") is **not** the risk; the explicit lock/algorithm clause and the surrounding transaction pattern are.

---

**Do not merge.** Re-submit as `ALGORITHM=INSTANT, LOCK=DEFAULT` (verified on 8.0.36), or with a controlled online-schema-change plan and an MDL / lock-timeout / settlement-job guard.