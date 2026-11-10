import os
import socket
import threading
import time
from collections.abc import Iterator
from urllib.parse import urlsplit, urlunsplit

import psycopg
import pytest
import uvicorn
from psycopg_pool import ConnectionPool

from bulk_import import db
from bulk_import.main import app


def _test_database_url() -> str:
    url = os.environ.get("TEST_DATABASE_URL")
    if not url:
        raise RuntimeError("TEST_DATABASE_URL is not set")
    return url


def _ensure_database_exists(url: str) -> None:
    """Create the test database if it is missing. Needs a connection to `postgres`."""
    parts = urlsplit(url)
    database = parts.path.lstrip("/")
    admin_url = urlunsplit(parts._replace(path="/postgres"))
    with psycopg.connect(admin_url, autocommit=True) as conn:
        exists = conn.execute(
            "SELECT 1 FROM pg_database WHERE datname = %s", (database,)
        ).fetchone()
        if exists is None:
            conn.execute(f'CREATE DATABASE "{database}"')


@pytest.fixture(scope="session")
def database_url() -> str:
    url = _test_database_url()
    _ensure_database_exists(url)
    return url


@pytest.fixture(scope="session")
def pool(database_url: str) -> Iterator[ConnectionPool]:
    pool = db.create_pool(database_url)
    pool.open(wait=True)
    db.apply_schema(pool)
    yield pool
    pool.close()


@pytest.fixture()
def db_conn(pool: ConnectionPool) -> Iterator[psycopg.Connection]:
    """A clean database for one test, and a connection to assert on it.

    The truncate is committed immediately: an uncommitted TRUNCATE holds an exclusive lock
    and would block the service's own inserts.
    """
    with pool.connection() as conn:
        conn.execute("TRUNCATE patients, clients CASCADE")
        conn.commit()
        yield conn
        conn.rollback()


@pytest.fixture()
def clean_db(db_conn: psycopg.Connection) -> psycopg.Connection:
    return db_conn


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


@pytest.fixture(scope="session")
def live_server(database_url: str) -> Iterator[str]:
    """A real uvicorn server: concurrent tests need real sockets and real connections."""
    os.environ["DATABASE_URL"] = database_url
    port = _free_port()
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()

    deadline = time.monotonic() + 30
    while not server.started:
        if time.monotonic() > deadline:
            raise RuntimeError("the test server did not start")
        time.sleep(0.05)

    yield f"http://127.0.0.1:{port}"

    server.should_exit = True
    thread.join(timeout=10)
