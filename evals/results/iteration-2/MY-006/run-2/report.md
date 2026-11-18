# Migration Guard Report — MY-006

## Verdict: 🔴 **HIGH RISK — DO NOT MERGE AS WRITTEN**

The developer's assumption ("It is just adding a column, so it should be fast") is contradicted by the migration's own options and by the observed production workload. The explicit `ALGORITHM=INPLACE, LOCK=SHARED` forces a **full table rebuild** of a 6.5M-row table and **blocks writes for the duration**, while also queueing behind a recurring long-running transaction on the primary — producing a classic metadata-lock (MDL) pile-up that can stall the highest-QPS query on the table.

---

## Evidence

### 1. The options over-ride the fast path

From `context.md`:

> "On MySQL 8.0, adding a column at the end with a constant default can use `ALGORITHM=INSTANT`, in which case only `LOCK=DEFAULT` is permitted. The developer has explicitly asked for `INPLACE` + `LOCK=SHARED`, which forces a rebuild and blocks writes for the duration instead of taking the instant path."

The migration itself:

```sql
ALTER TABLE `shipments`
  ADD COLUMN `status` VARCHAR(20) NOT NULL DEFAULT 'pending',
  ALGORITHM=INPLACE, LOCK=SHARED;
```

A trailing column with a constant default (`'pending'`) qualifies for INSTANT under 8.0.36. Demanding `INPLACE` achieves the opposite of the stated intent: it converts an operation lasting milliseconds into an online table rebuild.

### 2. The table is large and write-active

From `schema.sql`:

> "-- Rows: ~6,500,000. Inserts: ~25/second."

A rebuild of 6.5M rows on the primary stays in-flight well after the build phase while it drains the online change buffer, and `LOCK=SHARED` permits **no writes** while the ALTER holds the exclusive phase.

### 3. A recurring long-running transaction holds an MDL on the same table

From `application-usage.md`:

> "It holds a shared metadata lock on `shipments` for the whole 2–6 minutes."
> Runs every 15 minutes **on the primary** (no read replica routing configured for this command).

The `SettlementReport` command wraps `lockForShare()` over the whole table in a `DB::transaction` and spends "2–6 minutes of aggregation in PHP" inside it. MySQL holds metadata locks until transaction end.

### 4. The highest-QPS query is on this table

From `application-usage.md`:

> "`SELECT … WHERE id = ?` per request. In production this is the highest-QPS query on the table."

### 5. The pile-up mechanism

From `context.md`:

> "MySQL metadata locks are held for the duration of a transaction; a queued `ALTER` waiting for an MDL serialises later requests on the same table, which is the classic MDL pile-up."

Once the ALTER enters the MDL queue waiting on the settlement transaction, **every subsequent query on `shipments`** — including the hot point lookup — queues behind the pending *exclusive* MDL request. A 2–6 minute settlement window becomes a 2–6 minute application-wide outage of the order-tracking path, possibly twice in a row if the ALTER misses one window and waits for the next.

---

## Risk summary

| Concern | Assessment |
|---|---|
| Algorithm chosen | `INPLACE` forces rebuild; INSTANT is available on 8.0.36 |
| Locking | `LOCK=SHARED` blocks writes; not needed for INSTANT |
| MDL contention | Will queue behind the 2–6 min settlement transaction |
| Blast radius | Blocks all reads/writes on `shipments` — the hot path — while queued |
| Reversibility | Rebuild is slow; aborting mid-rebuild adds further load |
| Verdict | **HIGH** — not "just a column" |

---

## Required changes before merge

1. **Use the instant path.** Verify on the target server, then drop the explicit algorithm/lock:
   ```sql
   ALTER TABLE `shipments`
     ADD COLUMN `status` VARCHAR(20) NOT NULL DEFAULT 'pending',
     ALGORITHM=INSTANT, LOCK=NONE;
   ```
   Confirm the server actually reports `ALGORITHM=INSTANT` (8.0.29+ supports adding trailing columns instantly; note the pre-8.0.29 caveat where columns are appended *and* returned instantly only under 8.0.29+). If for any reason INSTANT is not chosen, **abort** rather than silently falling back to a rebuild.

2. **Set a short lock-wait and retry.** Even an INSTANT ALTER needs a moment of exclusive MDL. Use:
   ```sql
   SET SESSION lock_wait_timeout = 5;
   ```
   with a retry loop, and run the migration **outside** the settlement window (e.g., immediately after a settlement transaction commits).

3. **Fix the root cause (separate ticket).** The `SettlementReport` command should not hold a table-wide `lockForShare()` for minutes. Route it to the replica or break it into short batched transactions. As long as it runs on the primary every 15 minutes, *any* DDL on `shipments` carries this risk.

4. **Coordination note.** The new default (`'pending'`) covers older code that inserts without `status` — that is correctly designed and does not change the risk assessment.

---

## Statement

The migration is an **MDL pile-up waiting to happen**. It uses the slow, write-blocking algorithm when a fast one is available, on a large, write-hot table whose MDL is already held for minutes at a time by a known transaction on the primary. Because the pile-up can freeze the highest-QPS endpoint, this is **not** a `LOW`-risk change.

**Do not merge as written.** Switch to `ALGORITHM=INSTANT`, add a bounded `lock_wait_timeout` with retries, and schedule outside the settlement window.