## Migration Guard Report — PG-005

**Migration:** `db/migrations/0088_events_duration_ms_int.sql`
**Statement:** `ALTER TABLE events ALTER COLUMN duration_ms TYPE integer;`
**Verdict:** **HIGH** — do not merge as written.

---

### Finding 1 — Narrowing conversion will fail on existing data (HIGH)

`bigint → integer` is a narrowing change. PostgreSQL rejects out-of-range values rather than truncating, so the statement will error out once it reaches a row above `2147483647`.

Evidence from `schema.sql`:
- Developer measurement on 2026-11-16: `SELECT max(duration_ms), min(duration_ms) FROM events;  -> 4212849000, 0`
- `SELECT count(*) FROM events WHERE duration_ms > 2147483647;  -> 3,842`

3,842 rows exceed the integer ceiling, and the maximum value (4,212,849,000) is about twice `2^31-1`. The migration will abort. It is a hard error, not silent corruption, so recovery is possible. That is why this is HIGH and not BLOCKER.

### Finding 2 — The producer still writes out-of-range values (HIGH)

Narrowing the column does not fix the underlying writer. From `application-usage.md`:

- `src/events/import_sessions.py:37`
  ```python
  duration = (ended_at - started_at).total_seconds() * 1000  # can exceed 2^31 for multi-week sessions
  ```
  → "historical importer, writes values up to ~4.2e9."

Even after the existing bad rows are removed, the next import run will re-introduce values the column cannot hold. Unless the importer is changed or clamped in the same release, the migration just moves the failure from deploy time to runtime. The new write would error with `integer out of range`.

The stated justification, from `context.md`:
> "The mobile SDK never sends more than 60 seconds, so integer is plenty."

This only covers the SDK path (`src/events/ingest.py:52`, "observed range 0–60,000"). It does not cover the importer. The SDK is also marked **"Not searched"** in `application-usage.md` ("The mobile SDK repository (separate)"). So the claim about the SDK's declared type is unverified in this repository.

### Finding 3 — Full table rewrite on 48M rows (operational risk, HIGH)

`context.md`: "The type change rewrites the table."

`bigint → integer` has no binary-compatible cast path, so PostgreSQL rewrites every row, including in `payload`-adjacent storage and all indexes, under an `ACCESS EXCLUSIVE` lock. On an append-heavy 48M-row log this means a long lock, blocking writes for the whole rewrite, and WAL/bloat pressure. The 90-day retention job ("a job deletes rows older than 90 days nightly") does not reduce the live 48M set meaningfully.

### Finding 4 — Read-side consumers assume integer, but are compatible with bigint (informational)

- `src/api/events/serializers.py:31` returns `duration_ms` as an integer in JSON. A `bigint` maps fine to a JSON number; narrowing is not required for this.
- `dashboards/event_latency.sql:12` casts `duration_ms` to `numeric`. `numeric` handles either type.
- `src/events/aggregate.py:19` uses `avg()`, which returns `numeric` regardless.

None of the reads require the type change. The requested narrowing appears to be for tidiness only, and it is not part of an observed bug.

### Finding 5 — No `USING` clause (HIGH)

`context.md`: "The migration has no `USING` clause."

For `bigint → integer`, PostgreSQL uses an assignment cast; a `USING` clause would only add an explicit conversion step, and would not make out-of-range values succeed. So a `USING` clause on its own is not the fix here. It is evidence that the migration was written without checking what happens to the data.

---

### Recommendation

Do not merge. Options, in order of preference:

1. **Keep `bigint`.** Nothing in the read path requires `integer` (Finding 4). Fix the producer instead: clamp or split `import_sessions.py` durations, or store them differently.
2. **If narrowing is truly required**, run in this order in one coordinated release:
   - change `import_sessions.py` to write in-range values (or move long sessions to a separate column);
   - run a batched backfill that updates or resets the 3,842 rows above `2147483647` (verify the count again at deploy time; retention deletes rows nightly, so the set changes);
   - then run the `ALTER`, with a `USING duration_ms::integer` and a validation step (`SELECT count(*) WHERE duration_ms > 2147483647` = 0) as a pre-condition;
   - plan the 48M-row rewrite: use a maintenance window or the online pattern (new column, backfill, swap), and account for the `ACCESS EXCLUSIVE` lock.
3. **Add a runtime guard**: CHECK constraint or application-level clamp so a future importer bug cannot error out production writes.

If the migration is merged and run anyway, it fails safely with an out-of-range error. No data is destroyed, but the deploy is blocked and the table may be partially rewritten depending on transaction handling.

**Verdict: HIGH.** Risk to data and operations, with recovery possible. It is not a silent corruption, not LOW, and not BLOCKER.