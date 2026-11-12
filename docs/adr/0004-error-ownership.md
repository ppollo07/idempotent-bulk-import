# 0004. An error belongs either to the record or to the connection

**Status:** Accepted

## Context

A batch mixes records that are wrong with infrastructure that can break mid-request. PostgreSQL can
reject a value that passed validation (a NUL byte in a text column), and a foreign key can fail if
the parent row disappears between the lookup and the insert. A batch-wide crash used to answer
`500` with no results, losing the outcome of the records that had already committed.

The tempting fix — wrap every database exception and return `failed` for that record — is worse
than the bug: it tells the caller that a record is bad when in fact the database was down and the
record was never attempted.

## Decision

Two families, two answers:

| Exception family | Whose problem it is | Answer |
|---|---|---|
| `DataError`, `IntegrityError` (incl. `ForeignKeyViolation`) | the **record** | `failed` for that record, with a code that says which: `validation_error`, `owner_not_found`, `database_error` |
| `OperationalError`, `InterfaceError`, `PoolTimeout` | the **connection** | `503`, no per-record results at all |

`503` means "this batch was interrupted": the records committed before the failure stay committed,
and their ids were lost with the response. Retry it. That is safe because the write path is
idempotent — the prefix comes back as `already_existed` with the same ids, and the rest is applied.
"Nothing was attempted" would be the wrong thing to say, and it was said here until a reviewer
pointed out that it cannot be true.

Non-whitespace control characters (`NUL`, `0x01`…) are **rejected, never stripped**. Silently
rewriting a value contradicts ADR 0002, and a NUL byte in a name is corrupted data, not cosmetic
noise. Whitespace is the one thing normalisation does collapse, which is a separate rule. The
`DataError` catch remains as the net for whatever validation cannot predict.

## Consequences

- A poison record can no longer destroy the outcome of its neighbours.
- A database outage no longer masquerades as a data problem, which means the migration team is not
  sent to look for bad records that do not exist.
- The caller gets one clear instruction on `503`: retry the batch. Nothing to reconcile by hand.
