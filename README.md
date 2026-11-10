# Idempotent bulk import

Receiving side of a clinic data migration: an HTTP service with bulk-create endpoints for
clients (pet owners) and patients (their animals) that is always safe to retry.

Stack: Python 3.12, FastAPI, psycopg 3 with raw SQL, PostgreSQL 16, pytest, Docker Compose.

## Run

```bash
docker compose up --build
curl localhost:8000/health
```

## Test

```bash
docker compose run --rm tests
```

Everything runs in Docker; the database connection is set by `compose.yaml`:

- `DATABASE_URL=postgresql://postgres:postgres@db:5432/bulk_import`
- `TEST_DATABASE_URL=postgresql://postgres:postgres@db:5432/bulk_import_test`

> The README is completed during the build: design decisions, AI usage, what was cut and
> what comes next.
