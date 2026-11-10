# Idempotent bulk import

The receiving side of a clinic migration: an HTTP service that accepts clients (pet owners)
and patients (their animals) in batches, and is always safe to retry. Sending the same records
again — one after another, or from ten workers at the same time — never creates a duplicate,
and every answer carries the same ids as the first one.

Python 3.12, FastAPI, psycopg 3 with raw SQL, PostgreSQL 16, pytest. Everything runs in
Docker; the only prerequisite is **Docker with the Compose plugin**.

## Run

```bash
docker compose up --build
```

```bash
curl localhost:8000/health
# {"status":"ok"}
```

The service is on <http://localhost:8000>, with interactive docs at
<http://localhost:8000/docs>. The schema is applied at startup (`CREATE TABLE IF NOT EXISTS`).

## Test

```bash
docker compose run --rm tests
```

That runs pytest against a **separate database** in the same PostgreSQL container
(`bulk_import_test`, created by the test session, truncated between tests). The concurrency
tests start a real uvicorn server on a free port and fire their requests from real threads over
sockets, because `TestClient` calls the app in-process and cannot show two writers racing on
the same rows.

The suite repeats the concurrent-duplicate test five times: a race that did not happen once
proves nothing.

## API

Two endpoints, one behaviour. `POST /clients/bulk` and `POST /patients/bulk` take up to 1000
records, answer `200` with one result per record **in request order**, and never fail the batch
because of one bad record.

```bash
curl -s -X POST localhost:8000/clients/bulk -H 'content-type: application/json' -d '{
  "records": [
    {"source_id": "C-1", "first_name": "Ana", "last_name": "Ruiz", "email": "ana@example.com"},
    {"source_id": "C-2", "first_name": "Luis", "last_name": "Paz"}
  ]
}'
```

```json
{"summary": {"created": 2, "already_existed": 0, "conflict": 0, "failed": 0},
 "results": [
   {"index": 0, "source_id": "C-1", "status": "created", "id": "8f526627-4cde-416e-afdb-5a7252e66548", "error": null},
   {"index": 1, "source_id": "C-2", "status": "created", "id": "2f8a1193-503e-464b-83a7-39914176f004", "error": null}
 ]}
```

Sending the same record again:

```json
{"summary": {"created": 0, "already_existed": 1, "conflict": 0, "failed": 0},
 "results": [{"index": 0, "source_id": "C-1", "status": "already_existed",
              "id": "8f526627-4cde-416e-afdb-5a7252e66548", "error": null}]}
```

The same `source_id` with different data:

```json
{"results": [{"index": 0, "source_id": "C-1", "status": "conflict",
              "id": "8f526627-4cde-416e-afdb-5a7252e66548",
              "error": {"code": "payload_mismatch", "message": "source_id already exists with different data"}}]}
```

Patients reference their owner by `source_id`, and a missing owner fails only that record:

```bash
curl -s -X POST localhost:8000/patients/bulk -H 'content-type: application/json' -d '{
  "records": [
    {"source_id": "P-1", "name": "Luna", "species": "dog", "date_of_birth": "2020-01-02", "owner_source_id": "C-1"},
    {"source_id": "P-2", "name": "Michi", "species": "cat", "owner_source_id": "C-404"}
  ]
}'
```

```json
{"summary": {"created": 1, "already_existed": 0, "conflict": 0, "failed": 1},
 "results": [
   {"index": 0, "source_id": "P-1", "status": "created", "id": "a1baf068-6f92-4950-8834-facfadbe1fef", "error": null},
   {"index": 1, "source_id": "P-2", "status": "failed", "id": null,
    "error": {"code": "owner_not_found", "message": "owner_source_id C-404 does not exist"}}
 ]}
```

| `status` | `id` | `error.code` | Meaning |
|---|---|---|---|
| `created` | new uuid | `null` | written by this request |
| `already_existed` | original uuid | `null` | same data, nothing written |
| `conflict` | original uuid | `payload_mismatch` | same `source_id`, different data, nothing overwritten |
| `failed` | `null` | `validation_error`, `owner_not_found` | rejected, nothing written |

`422` is reserved for a malformed **envelope** (missing `records`, not a list, empty, more than
1000). A bad field inside a record is a per-record `failed`, so one broken row never rejects
the other 999.

## How it is correct

Six decisions, one page each in [`docs/adr`](docs/adr). The short version:

1. **Deduplicate on the business key, enforced by the database**
   ([0001](docs/adr/0001-dedup-on-source-id.md)). `UNIQUE (source_id)` + `INSERT ... ON CONFLICT
   (source_id) DO NOTHING RETURNING id`. A request-level `Idempotency-Key` would only protect
   one request: two workers, or a tool that rebuilds a batch after a crash, send the same
   records under different keys and different batch boundaries. Checking before inserting is a
   race; a `UNIQUE` constraint is not. `tests/concurrency/test_check_then_insert_is_a_race.py`
   runs the naive version and produces the duplicate, so the claim is demonstrated, not
   asserted.
2. **`ON CONFLICT DO NOTHING` returns no row** for an existing record, so a second `SELECT`
   fetches the committed row. It runs in `READ COMMITTED`, where each statement takes a fresh
   snapshot and therefore sees the row the other worker committed moments ago.
3. **Resent with different data is a per-record `conflict`, never an overwrite**
   ([0002](docs/adr/0002-conflict-policy.md)). A `payload_hash` (SHA-256 of the normalised
   business fields) decides between `already_existed` and `conflict`. An overwrite would not be
   safe to retry: a late retry of an old batch would silently replace newer data and the result
   would depend on arrival order. In a migration, a visible error beats a silent change.
4. **Normalisation happens once, on ingress** (`src/bulk_import/normalize.py`). Strings are NFC,
   trimmed, internal whitespace collapsed; `email` and `species` are also lower-cased; dates are
   ISO; an absent optional field, `null` and `""` are the same data. A delta run that exports
   `"Ana "` instead of `"Ana"` is `already_existed`, not a wall of false conflicts. Nothing is
   fuzzy-matched: accents, synonyms and near-duplicates stay different, because a `conflict` is
   the only signal that the source changed something.
5. **One short transaction per record** ([0003](docs/adr/0003-per-record-transaction.md)).
   Per-record outcomes come for free, a bad record cannot roll back the batch, and two workers
   sending the same batch in a different order cannot deadlock on it.
6. **Minimal validation.** Required fields present and non-empty after normalisation, bounded
   length, `date_of_birth` parseable, `owner_source_id` resolvable; validation runs before the
   database is touched, so `validation_error` always wins over `owner_not_found`. Email format
   is deliberately **not** validated: the brief asks for it to be optional, and failing a real
   client over an address we cannot verify would block a migration for no gain.

The module layout is deliberately flat — `main.py` (routes and pool), `schemas.py` (Pydantic),
`normalize.py`, `import_service.py` (one transaction per record), `db.py`, `schema.sql`. For a
two-hour exercise, ports, protocols and repository classes would add surfaces to explain without
adding a guarantee. In Provet this would be a Django app; the core (`UNIQUE` + `ON CONFLICT` +
per-record transaction) carries over unchanged.

## Not built, deliberately

The brief asked for a line or two on each:

- **Owners arriving after their patients, or in the same request.** Park the patient in a
  `pending_owner` table keyed by `owner_source_id` and retry the resolution when the owner
  arrives (or at the end of the run), instead of failing it. The batch contract would not
  change; only the record's disposition would move from `failed` to `pending`.
- **Throttling the migration tool when the service is overloaded.** Answer `429` with
  `Retry-After` when the connection pool or the database is saturated, and keep a bounded queue:
  a migration tool that retries with backoff is the cheapest rate limiter there is.
- **Reporting progress across a run that takes days.** A `runs` table with a row per run and
  per-record outcomes, plus a `GET /runs/{id}` endpoint returning counts by status and the first
  failures. The per-record results already carry everything such a report needs.

## How I used AI

Briefly: I used an AI assistant as a pair for design review, for writing the test suite first,
and for this documentation; every design decision in this README is one I can defend, and the
mistakes it made are listed in [`docs/ai-usage.md`](docs/ai-usage.md) with how I caught them.
The two worth naming here: it produced a Pydantic field typed `Any` for `date_of_birth` (which
would have accepted garbage dates instead of failing the record), and its first test fixture
committed the `TRUNCATE` only at teardown, which holds an exclusive lock and blocks the
service's own inserts. Both were caught by running the code, not by reading it.

## What I would do next, and what I cut

Next, in order:

1. **Versioned updates for delta runs.** A real delta changes data. Today a changed record is a
   visible `conflict`; the production answer is an explicit update applied only when the legacy
   `source_updated_at` is newer than what we stored, so replay order stops mattering.
2. **Transactional outbox.** Write the `client.created` event in the same transaction as the
   row (see [`docs/written-answer.md`](docs/written-answer.md)), with a relay and an
   idempotent consumer. That was the one stretch item I did not build.
3. **Alembic instead of `schema.sql` at startup**, and a connection pool sized from
   configuration instead of a constant.
4. **`429` + `Retry-After` and a bounded queue**, and a `runs` table with a progress endpoint.
5. **An `Idempotency-Key` header** for callers that want the identical response replayed. It is
   not the dedup mechanism, so it is not in the core.

Cut on purpose: the outbox publisher, CI, authentication, tenancy (`UNIQUE (tenant_id,
source_id)` in production), read endpoints, metrics, and a `pending_owner` table. The written
answer and the "Not built" section above describe each one in the place it belongs.

## Time spent

Honest numbers, in the README because the brief asked for them.

| Phase | Start | End |
|---|---|---|
| Scaffold, Compose and schema | 14:24 | 14:36 |
| Normalisation, domain tests, client endpoint (test-first) | 14:36 | 14:44 |
| Concurrency tests and patient endpoint | 14:44 | 14:52 |
| Conflict policy, negative control, envelope contract | 14:52 | 15:00 |
| README, ADRs, written answer, AI notes | 15:00 | 15:25 |

About **1 hour** of clock, inside the 2-hour timebox. The scaffold and Compose were prepared
before the clock started, as the brief allows; the deliverables are the code, the tests, this
README, the ADRs and the written answer.
