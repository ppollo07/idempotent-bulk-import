# Written answer: the notification after the write commits

**Question.** After a record is created, the system must notify another service (for example,
to send the client a welcome email). That call happens after your write has committed, and it
can fail. What do you return to the migration tool, how do you make sure the notification isn't
lost, and what happens when the migration tool retries?

## What I return

Success, the moment the record and its notification event commit together. The email is not
part of the import contract: the record exists, so pretending the request failed would be a lie,
and it would push the migration tool to retry something that already succeeded.

## How the notification isn't lost

A transactional outbox. In the same transaction as the client row, I insert a row into an
`outbox` table: event id, `aggregate_type = 'client'`, `aggregate_id`, `event_type =
'client.created'`, payload, `created_at`, `published_at = null`. Either both rows exist or
neither does, so there is no window where the record is committed and the event is forgotten.

A separate publisher polls unsent rows (`FOR UPDATE SKIP LOCKED`, so several publishers can run
without stepping on each other), calls the notification service, and sets `published_at` only
after the call returns. Failures are retried with exponential backoff; after N attempts the row
is parked with an alert and stays visible, because a dead email queue must not silently swallow
a welcome email. At scale the same table is read by a log-tailing relay (Debezium's outbox event
router) instead of a polling loop; the table shape is compatible.

Delivery is **at least once**: the publisher can crash after the call but before marking the row,
and it will send again. That is the right side of the tradeoff — a duplicated email is
recoverable, a lost one is not.

## What the duplicate would cost, and where it is closed

The event id travels with the call and the consumer deduplicates on it (idempotent consumer: it
records processed event ids and discards repeats). That closes the common case, not every case:
if the consumer **sends the email and crashes before recording the id**, the retry sends it again.
Only the provider's own idempotency key — or a `sent_at` written before the send, which buys the
opposite risk — closes that one, and it is a decision for whoever owns the notification service.
So the honest claim is "a lost notification is impossible once the record commits; a duplicate is
possible and bounded by the consumer's deduplication", not "two emails can never happen".

## What happens when the migration tool retries

The record already exists, so the answer is `already_existed` with the same id. No insert
happens, therefore no new outbox row is written and no second event is produced. The first
event is delivered on its own schedule, unaffected by the retry.

## Why not call the notifier inline

If the call happens after the commit and fails, the only honest options are worse: report a
failure for data that was saved (and invite a retry that changes nothing), or swallow the error
and lose the email. If the call moves inside the transaction instead, the notification service
becomes part of the commit path, so its latency and availability decide whether a clinic can be
migrated. The outbox keeps the two concerns independent and makes the loss of the **event**
impossible: once the record commits, the event commits with it. Delivery stays at-least-once.
