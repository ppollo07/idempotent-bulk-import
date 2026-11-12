# Written answer: the notification after the write commits

**What I return.** Success, as soon as the record and its notification event commit together. The
email is not part of the import contract: the record exists, so reporting a failure would be a lie
and would invite a retry that changes nothing.

**How it is not lost.** Transactional outbox: an `outbox` row — event id, aggregate id,
`client.created`, payload — is written in the same transaction as the client row. Either both exist
or neither does, so there is no window where the record commits and the event is forgotten. A
separate relay reads unpublished rows with `FOR UPDATE SKIP LOCKED` (so several relays can run),
calls the notification service, and sets `published_at` only after the call returns. Failures retry
with backoff, then park with an alert. At scale the same table is read by a log-tailing relay
instead of a polling loop.

**Where the duplicate window is.** Delivery is at-least-once: the relay can crash after the call and
before marking the row, so it sends again. The event id travels with the call and the consumer
deduplicates on it, which closes the common case. It does not close the hard one: if the consumer
sends the email and crashes before recording the id, the retry sends it twice. Only the provider's
own idempotency key — or a `sent_at` written before the send, which buys the opposite risk — closes
that, and it belongs to whoever owns the notification service. So: a lost notification is impossible
once the record commits; a duplicate is possible and bounded by the consumer's deduplication, not
eliminated.

**What happens when the migration tool retries.** The record already exists, so the answer is
`already_existed` with the same id. No insert, therefore no new outbox row and no second event. The
original event is delivered on its own schedule.

**Why not call the notifier inline.** If the call happens after the commit and fails, the only
options are worse: report a failure for data that was saved, or lose the email. If it moves inside
the transaction, the notification service's latency and availability decide whether a clinic can be
migrated.
