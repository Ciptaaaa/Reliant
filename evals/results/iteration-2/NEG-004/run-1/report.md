# Migration Guard Report

## Case: NEG-004 — `add_currency_to_invoices`

**Migration:** `db/migrate/20261116112000_add_currency_to_invoices.sql`
**Statement:**
```sql
ALTER TABLE invoices ADD COLUMN currency varchar(3) NOT NULL DEFAULT 'USD';
```

---

## Verdict: ✅ APPROVED

**Risk level: LOW**

---

## Analysis

### 1. Access-exclusive lock / rewrite risk — NOT present
Declaring the column `NOT NULL` with a **non-volatile constant default** on PostgreSQL 11+ is a
**metadata-only** operation. No table rewrite and no full scan of the ~40,000 rows occurs. Existing rows
will return `'USD'` on read from the stored default, without physical modification.

Evidence:
- `-- Current schema (PostgreSQL 16)` — confirms engine version.
- `-- PostgreSQL 16 (confirmed in docker-compose.yml).` — version re-confirmed.
- `The default is a literal constant ('USD'), not a volatile function.` — this is the precise trigger
  condition (non-volatile default) that keeps the operation metadata-only.
- `No NOT NULL is being added to an existing column; the column itself is new.` — so no full-table
  validation scan for the not-null constraint is required.

Note the version dependency explicitly: on **PG 10 and older**, an add-column with default **does**
rewrite the table. The version facts above confirm we are on 16, so this concern does not apply here.

### 2. Backfill requirement — NONE
The default is stored in the column metadata and applied on read to existing rows. A backfill would
only be needed if the intent were to physically materialize `'USD'` into existing rows (e.g., before
removing the default or changing the type), which is not the case here.

Evidence:
- `-- lib/tasks/backfill_invoices.rake — does not exist; no backfill is required because the default is
  stored in metadata and applied to existing rows on read.`

### 3. Application compatibility — SAFE across rolling deploy
Existing inserts do not specify `currency`; they will receive `'USD'` from the database default. No
serializer or insert path currently depends on the column in a way that breaks the rolling deploy.

Evidence:
- `app/serializers/invoice_serializer.rb:14 — will include currency once merged; existing serializer
  does not reference it and inserts do not specify it, so they receive 'USD'.`

### 4. Index / constraint impact — NONE
No index or constraint references `currency`, so there are no dependent objects to rebuild or
invalidate.

Evidence:
- `No index or constraint references currency.`

### 5. Lock duration concern — NEGLIGIBLE
Only a brief `ACCESS EXCLUSIVE` lock is needed to add the catalog entry. With no rewrite and no
long-running transactions (context states "no long transactions"), the lock is held momentarily.

---

## Findings

| Severity | Finding |
|---|---|
| LOW | Add-column with constant default is metadata-only on PG 11+ (confirmed PG 16). The one caveat: on PG 10 and older this statement **would** rewrite the table. Verified version via `docker-compose.yml`, so acceptable. |

## Recommendation
**Merge.** No backfill, no rewrite, no scan. The version-gated caveat (PG ≥ 11) is satisfied by the
confirmed PostgreSQL 16 target.