"""Database wiring: the connection pool and the schema.

There is no ORM on purpose. The idempotency guarantee lives in the UNIQUE constraint and in
``INSERT ... ON CONFLICT``, so those statements must stay visible in the code.
"""

import os
from pathlib import Path

from psycopg_pool import ConnectionPool

SCHEMA_PATH = Path(__file__).with_name("schema.sql")

#: Ten concurrent duplicate senders must each get a real connection, otherwise the
#: concurrency tests would serialise the requests and prove nothing.
POOL_MAX_SIZE = 20


def database_url() -> str:
    url = os.environ.get("DATABASE_URL")
    if not url:
        raise RuntimeError("DATABASE_URL is not set")
    return url


def create_pool(url: str) -> ConnectionPool:
    return ConnectionPool(url, min_size=2, max_size=POOL_MAX_SIZE, open=False)


def apply_schema(pool: ConnectionPool) -> None:
    statements = SCHEMA_PATH.read_text(encoding="utf-8")
    with pool.connection() as conn:
        conn.execute(statements)
