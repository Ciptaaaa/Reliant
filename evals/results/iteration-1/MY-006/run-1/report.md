# Migration Guard Report — MY-006

**Migration:** `database/migrations/2026_11_16_125000_shipments_add_status.php`
**Table:** `shipments` (~6.5M rows, ~25 inserts/sec)
**Statement:**

```sql
ALTER TABLE `shipments`
  ADD COLUMN `status` VARCHAR(20) NOT NULL DEFAULT 'pending',
  ALGORITHM=INPLACE, LOCK=SHARED;
```

---

## Verdict: ❌ **DO NOT MERGE — HIGH RISK**

The "it is just adding a column, so it should be fast" reasoning is **wrong on this engine and this workload.** Two independent hazards combine into a write-outage scenario.

---

## Findings

### 1. The explicit `ALGORITHM`/`LOCK` clause defeats the instant path (CRITICAL)

Per the deployment facts in `context.md`:

> "On MySQL 8.0, adding a column at the end with a constant default can use `ALGORITHM=INSTANT`, in which case only `LOCK=DEFAULT` is permitted. The developer has explicitly asked for `INPLACE` + `LOCK=SHARED`, which forces a rebuild…"

Adding a trailing `VARCHAR(20) NOT NULL DEFAULT 'pending'` is exactly the instant-eligible case (append-only, constant default). The developer's explicit `ALGORITHM=INPLACE, LOCK=SHARED` **overrides the instant path and forces a full table rebuild** of ~6.5M rows. This is the opposite of the safe choice.

- `LOCK=SHARED` permits reads but **blocks all writes** for the duration of the rebuild.
- Inserts run at **~25/sec** (`schema.sql`), so blocked writes accumulate for the entire rebuild window.

### 2. Metadata-lock pile-up behind the settlement transaction (HIGH)

From `application-usage.md`:

> "It holds a shared metadata lock on `shipments` for the whole 2–6 minutes."
> "Runs every 15 minutes **on the primary** (no read replica routing…)."

Sequence of events:
1. The settlement `DB::transaction` acquires a **shared MDL** on `shipments` and holds it 2–6 minutes.
2. The `ALTER` requests an **exclusive MDL** for the rebuild and **queues** behind that transaction.
3. InnoDB's MDL queue is FIFO: **every subsequent query on `shipments`** — including the hottest query in the app (`ShipmentTrackingController.php:19`, "the highest-QPS query on the table") and all inserts — **queues behind the ALTER request**.
4. The result is a full read+write stall on `shipments` for the remainder of the settlement transaction (up to 6 minutes), repeated if the ALTER hasn't completed before the next 15-minute window.

This is the classic MDL pile-up described in `context.md`:

> "a queued `ALTER` waiting for an MDL serialises later requests on the same table, which is the classic MDL pile-up."

Because the settlement command is on the **primary** (no replica routing), there is no escape hatch — the ALTER cannot avoid the contended window.

Together, (1) + (2) means: writes are blocked by the rebuild, and the rebuild itself is stuck behind a 2–6 minute transaction while blocking the app's highest-QPS query.

---

## Required changes before merge

### Option A — Use the instant path (preferred)

```sql
ALTER TABLE `shipments`
  ADD COLUMN `status` VARCHAR(20) NOT NULL DEFAULT 'pending',
  ALGORITHM=INSTANT;
```

- Drop `LOCK=SHARED` — INSTANT only permits `LOCK=DEFAULT`.
- **Verify on the actual instance** (not just the documented version) that the operation is truly instant-eligible:

```sql
-- Pre-flight on a copy / staging with production-like schema:
ALTER TABLE `shipments`
  ADD COLUMN `status` VARCHAR(20) NOT NULL DEFAULT 'pending', ALGORITHM=INSTANT;
-- If this errors with "does not support ALGORITHM=INSTANT", fall back to Option B.
```

- Note: instant add on 8.0 has limits (row version count, `ALGORITHM=INSTANT` support for position). Confirm with `SELECT * FROM information_schema.INNODB_TABLES …` / test on clone.

### Option B — If instant is not available

If for any reason INSTANT is not usable, the ALTER must **not** run blind against the settlement window:

1. **Coordinate the window**: run the migration in a maintenance window where the settlement command is paused, OR temporarily route the settlement command to the replica.
2. **Add MDL timeout + retry** at the client:

```sql
SET SESSION lock_wait_timeout = 5;   -- fail fast instead of piling up
-- retry with backoff; abort if settlement is active
```

3. Prefer a tool that bounds the lock (e.g. `pt-online-schema-change` / `gh-ost`) if you must do it online, and throttle it below replica and primary capacity.

### Regardless of option

- **Do not** merge the current statement. It takes the worst available path (rebuild) against the worst background load (2–6 min MDL holder on the primary, every 15 min).
- Update the developer note: "adding a column" is only fast **if you let it be instant**; the explicit `INPLACE`/`LOCK=SHARED` clause is what makes it slow.

---

## Risk summary

| Factor | Evidence | Severity |
|---|---|---|
| Forces full rebuild on 6.5M rows | `context.md` deployment facts; explicit `ALGORITHM=INPLACE` | Critical |
| Blocks all writes during rebuild | `LOCK=SHARED` | High |
| Queues behind 2–6 min settlement MDL | `application-usage.md` `SettlementReport.php:58` | High |
| Piles up the app's highest-QPS read | `ShipmentTrackingController.php:19` | High |
| No replica escape (settlement on primary) | `application-usage.md` | High |

**Classification:** This is **not LOW** — it is "adding a column" only in appearance. On this MySQL version and this workload it is a write-blocking rebuild that serialises the application behind a long-held metadata lock.

**Action:** Reject and replace with `ALGORITHM=INSTANT` (verified on the actual instance), or reschedule with an MDL-timeout/retry strategy and the settlement job paused.