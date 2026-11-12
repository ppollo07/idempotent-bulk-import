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


#: Arbitrary constant, shared only by the instances of this service.
SCHEMA_LOCK_KEY = 728_917_331


def apply_schema(pool: ConnectionPool) -> None:
    """Apply schema.sql, serialised across instances.

    ``CREATE TABLE IF NOT EXISTS`` is not safe under concurrency: two sessions both look at the
    catalog, neither sees the relation, both create it, and the loser dies on a duplicate key in
    ``pg_type``. With several instances starting at once that kills a container on every fresh
    deploy. An advisory lock held for the transaction serialises it; ``pg_advisory_xact_lock``
    releases itself on commit, so a crashed instance cannot leave the schema blocked.
    """
    statements = SCHEMA_PATH.read_text(encoding="utf-8")
    with pool.connection() as conn, conn.transaction():
        conn.execute("SELECT pg_advisory_xact_lock(%s)", (SCHEMA_LOCK_KEY,))
        conn.execute(statements)
