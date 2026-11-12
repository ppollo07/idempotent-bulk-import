"""Several instances starting against an empty database must not race on the catalog.

`CREATE TABLE IF NOT EXISTS` is only "if not exists" for one session at a time. Three instances
starting together on a fresh database are the documented failure: two of the three die with a
duplicate key in `pg_type` and the container never comes up.
"""

import threading
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import urlsplit, urlunsplit

import psycopg
import pytest
from psycopg_pool import ConnectionPool

from bulk_import import db

SCRATCH = "bulk_import_test_ddl"
INSTANCES = 3
ROUNDS = 3


def _admin(base_url: str) -> str:
    parts = urlsplit(base_url)
    return urlunsplit(parts._replace(path="/postgres"))


def _execute_as_admin(base_url: str, statement: str) -> None:
    with psycopg.connect(_admin(base_url), autocommit=True) as conn:
        conn.execute(statement)


@pytest.fixture()
def scratch_database(database_url: str):
    _execute_as_admin(database_url, f'DROP DATABASE IF EXISTS "{SCRATCH}" WITH (FORCE)')
    _execute_as_admin(database_url, f'CREATE DATABASE "{SCRATCH}"')
    parts = urlsplit(database_url)
    yield urlunsplit(parts._replace(path=f"/{SCRATCH}"))
    _execute_as_admin(database_url, f'DROP DATABASE IF EXISTS "{SCRATCH}" WITH (FORCE)')


def _start_one(url: str, barrier: threading.Barrier) -> None:
    pool = db.create_pool(url)
    pool.open(wait=True)
    try:
        barrier.wait(timeout=30)
        db.apply_schema(pool)
    finally:
        pool.close()


def test_instances_can_apply_the_schema_at_the_same_time(scratch_database: str) -> None:
    for _ in range(ROUNDS):
        with psycopg.connect(scratch_database, autocommit=True) as conn:
            conn.execute("DROP TABLE IF EXISTS patients, clients CASCADE")

        barrier = threading.Barrier(INSTANCES)
        with ThreadPoolExecutor(max_workers=INSTANCES) as executor:
            futures = [
                executor.submit(_start_one, scratch_database, barrier) for _ in range(INSTANCES)
            ]
            for future in futures:
                future.result()  # raises if any instance died, which is the bug

        with psycopg.connect(scratch_database) as conn:
            tables = {
                row[0]
                for row in conn.execute(
                    "SELECT tablename FROM pg_tables WHERE schemaname = 'public'"
                )
            }

        assert {"clients", "patients"} <= tables, tables
