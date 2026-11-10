# 0003. One short transaction per record

**Status:** Accepted

## Context

A batch mixes good records with ones that fail: a missing owner, an empty field, a record that
conflicts. The brief requires a per-record outcome and requires that a failing record does not
stop the rest of the batch. Later, a notification will have to be written atomically with the
record (the written answer).

## Decision

Each record is processed in its own transaction, taken from the pool:

```python
with pool.connection() as conn, conn.transaction():
    ...  # the INSERT ... ON CONFLICT, and later the outbox row
```

## Why not one transaction per batch

- **Partial success.** A batch-wide transaction either writes everything or nothing, so a single
  bad record would reject the other 999 and the response could not report per-record outcomes.
- **Deadlocks.** Two workers sending the same batch in a different order take row locks in a
  different order inside one long transaction. Per-record transactions release each lock almost
  immediately, so the overlap never accumulates into a cycle. A test sends two overlapping
  batches in reverse order and requires two `200`s and zero duplicates.
- **Failure blast radius.** A connection-level error costs one record, not the batch.

## Consequences

- More round trips per batch: one connection checkout and one commit per record. Acceptable at
  migration scale; a production version would group with `SAVEPOINT`s if the volume demanded it.
- The outbox row for a created record goes in the same transaction as the row, which is exactly
  why the boundary is the record and not the request.
- A record that fails validation never opens a transaction at all: validation runs first, so
  `validation_error` always wins over `owner_not_found`.
