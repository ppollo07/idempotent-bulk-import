# 0001. Deduplicate on the business key, enforced by the database

**Status:** Accepted

## Context

The brief's failure modes are a lost response after the server did the work, a crashed tool
that resends its last batch, and two workers picking up the same batch. Runs are repeated: a
full load, then deltas. Whatever happens, a clinic must never end up with the same client or
patient twice.

## Decision

`UNIQUE (source_id)` on `clients` and on `patients`, and every write is
`INSERT ... ON CONFLICT (source_id) DO NOTHING RETURNING id`. If no row comes back, the record
already existed and a second `SELECT` returns the committed row.

`ON CONFLICT DO NOTHING` returns nothing for the conflicting row (PostgreSQL: "only rows that
were successfully inserted or updated will be returned"), which is why the follow-up `SELECT`
exists.

## Why not a request-level `Idempotency-Key`

A request key protects one request. Two workers, or a tool that rebuilds its batch after a
crash, send the same records under different keys or with different batch boundaries, and the
key does not see them as the same work. `source_id` is a stable business identifier per record,
so a natural key dedupes whatever the batching, the key or the number of workers.

## Why not check-then-insert

Two transactions both `SELECT`, both find nothing, both `INSERT`. Only a database constraint
closes that window; application-level checks cannot. The claim is demonstrated rather than
asserted: `tests/concurrency/test_check_then_insert_is_a_race.py` runs the naive version against
a table without the constraint and produces the duplicate, deterministically.

## Consequences

- The constraint is the guarantee; the code only translates its outcome. Removing it silently
  removes the property, so a test asserts it directly.
- `READ COMMITTED` (the psycopg default) is required, not incidental: after `DO NOTHING`
  returns nothing, the follow-up `SELECT` must see the row the other worker just committed.
  Under `REPEATABLE READ` that statement would use the older snapshot and could miss it.
- With several tenants the key becomes `UNIQUE (tenant_id, source_id)`. One clinic chain per
  database is the assumption of this exercise.
