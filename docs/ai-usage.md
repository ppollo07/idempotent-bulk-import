# How I used AI, and what I had to correct

I used an AI assistant (Claude) as a pair on this exercise: design discussion before writing,
the test suite first, and the prose in this repository. I reviewed and can defend every
decision here; the point of this file is the part a reviewer usually can't see — where the
assistant was wrong and how it got caught.

## Corrections, in the order they happened

1. **A field typed `Any` would have accepted garbage dates.** The first version of the request
   schema declared `date_of_birth: Any = None`, which silently accepts `"not-a-date"` and stores
   it. Replaced with `date | None`, so an unparseable date becomes a per-record
   `validation_error` instead of corrupt data. Caught by reading the diff before writing the
   test that sends an invalid date.
2. **A test fixture would have deadlocked the suite.** The assistant wrote the per-test cleanup
   as `TRUNCATE` inside the fixture, committed only when the fixture's connection was returned
   to the pool — after the test. An uncommitted `TRUNCATE` holds an exclusive lock, so the
   service's own inserts would have blocked and the test would have hung instead of failing.
   Fixed by committing the truncate before yielding the connection.
3. **`ruff format` was run against the wrong copy of the code.** It executed inside the test
   container, where the working tree is a copy baked into the image, so it reported "5 files
   reformatted" while `git status` stayed empty and `ruff check` kept failing on the host files.
   Noticed by the contradiction between the two commands; re-ran it with the repository mounted
   as a volume.
4. **A test assumed the wrong transaction semantics.** The constraint test inserted a row, then
   expected a second identical insert to raise `UniqueViolation` *and* leave the first row
   committed. It left zero rows: after a failed statement psycopg3 rolls back the implicit
   transaction. Fixed by committing the first insert explicitly and rolling back after the
   expected error. This one was useful — it is the same reason the production code never lets
   two records share a transaction.
5. **The pool was sized by instinct.** Ten concurrent senders against a pool with fewer than ten
   connections would have serialised the requests, and the concurrency test would have passed
   while proving nothing. `POOL_MAX_SIZE = 20` is now explicit, with a comment saying why.

## What I checked instead of trusting

- That a multi-statement `schema.sql` really executes through psycopg 3 (verified in the
  container, not assumed).
- That `ON CONFLICT DO NOTHING` returns no row for an existing key, which is why the follow-up
  `SELECT` exists — and that the follow-up sees the other worker's committed row, which is why
  the isolation level matters.
- That the negative control produces a duplicate: a concurrency test that cannot fail is not
  evidence.

## After the delivery: review passes over my own artifact

`v1.0.0` is what I produced inside the timebox. Everything after it came from review passes over
that artifact, and two of them were run by review agents: two blind, independent passes over the same
frozen state, with me merging the findings and fixing only what both confirmed. That is what the
`judgment` commits in the history are.

It is also the part of the exercise I would keep if I had to choose one, because the passes found
things I had written and never verified:

- a docstring in the negative control asserting a determinism its code did not have — the barrier was
  passed in and never awaited (`d841646`);
- a time table that `git log` refuted, removed and replaced by what the tag actually proves;
- ADR 0003 claiming that a connection error costs one record, while ADR 0004 and the code answered
  `503` for the whole batch (`4381321`);
- a `503` message saying "nothing is known about this batch" when a prefix of it may already be
  committed, now `7022a41`: a test that fails on the third record of five and asserts that the first
  two stayed in the database and come back as `already_existed` on the retry.

Each of those is a class of defect, not a typo: a claim that reads as verified and was not. The
mechanism that catches them is cheap — a second reader who did not write the code — and the reason to
write it down here is that the same mechanism is what I would put in front of a migration team.
