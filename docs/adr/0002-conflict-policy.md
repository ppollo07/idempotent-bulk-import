# 0002. A record resent with different data is a `conflict`, never an overwrite

**Status:** Accepted

## Context

The brief asks what happens when a record is resent with different data, and why. Runs repeat,
and a delta run can carry a genuinely changed record. The clinic's data becomes the source of
truth once it is live in the new system.

## Decision

Each row stores `payload_hash`: a SHA-256 of the canonical JSON of the normalised business
fields. On a repeat with the same `source_id`:

| Hash | Answer |
|---|---|
| equal | `already_existed`, with the original id |
| different | `conflict`, with the original id and `error.code = "payload_mismatch"` |

Nothing is overwritten in either case.

## Why not last-write-wins

- An overwrite is not safe to retry. A late retry of an old batch would replace newer data, and
  the final state would depend on arrival order rather than on the data.
- Silent data changes are the worst failure mode in a migration. A visible `conflict` is safe to
  retry, easy to triage, and gives the migration team a list to work through.
- Stripe, AWS and the IETF idempotency draft all reject a reused key with different parameters
  instead of applying the new ones.

## Why normalise before hashing

"Different" has to mean something. Normalisation runs once, on ingress (`normalize.py`):
Unicode NFC, trimmed, internal whitespace collapsed, `email` and `species` lower-cased, dates as
ISO, and an absent optional field treated as `null`. So a delta that exports `"Ana "` is
`already_existed`, while a changed surname is a `conflict`. What is stored is what is compared,
so there is exactly one definition of "the same data".

Nothing is fuzzy-matched. Every extra normalisation rule makes `conflict` rarer, and `conflict`
is the only signal that the source changed something; widening it is a decision for the
migration team, not for this endpoint.

## Consequences

- Delta runs that really change data need an explicit update path. The production answer is to
  apply an update only when the legacy `source_updated_at` is newer than the stored one, which
  is order-independent. That is scoped as the first item of "what I would do next".
- The stored row keeps the first values it was given. Re-sending corrected data does not fix it;
  it has to go through the update path, which is the point.
