"""Why the service never checks first and inserts later (D1).

This is the negative control for the concurrency tests in the other file: it shows that a
check-then-insert really does create duplicates when two workers race, with no UNIQUE
constraint to lose the race for them. Without this test, "the constraint is what makes it
safe" is a claim; with it, it is a demonstrated fact.

It is deterministic on purpose: the barrier sits *between* the check and the insert, so both
workers are guaranteed to have read "nothing" before either of them writes. Without it the race
is a timing coincidence, and a test that passes most of the time proves most of nothing.
"""

import threading
from concurrent.futures import ThreadPoolExecutor

import psycopg
import pytest

CREATE_SCRATCH = """
CREATE TABLE race_scratch (
    source_id text NOT NULL,
    first_name text NOT NULL,
    last_name text NOT NULL
)
"""


def naive_import(database_url: str, barrier: threading.Barrier, source_id: str) -> str:
    """Check, then insert: the classic race. No constraint on this table to save it."""
    with psycopg.connect(database_url) as conn:
        existing = conn.execute(
            "SELECT 1 FROM race_scratch WHERE source_id = %s", (source_id,)
        ).fetchone()
        if existing is not None:
            return "already_existed"
        # Both workers have read "nothing"; neither may write until both are here.
        barrier.wait(timeout=30)
        conn.execute(
            "INSERT INTO race_scratch (source_id, first_name, last_name) VALUES (%s, %s, %s)",
            (source_id, "Ana", "Ruiz"),
        )
        conn.commit()
        return "created"


@pytest.fixture()
def scratch_table(database_url: str):
    with psycopg.connect(database_url, autocommit=True) as conn:
        conn.execute("DROP TABLE IF EXISTS race_scratch")
        conn.execute(CREATE_SCRATCH)
    yield
    with psycopg.connect(database_url, autocommit=True) as conn:
        conn.execute("DROP TABLE IF EXISTS race_scratch")


def test_check_then_insert_creates_a_duplicate_when_two_workers_race(
    scratch_table, database_url: str
) -> None:
    barrier = threading.Barrier(2)

    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [executor.submit(naive_import, database_url, barrier, "C-1") for _ in range(2)]
        outcomes = [future.result() for future in futures]

    with psycopg.connect(database_url) as conn:
        rows = conn.execute("SELECT count(*) FROM race_scratch WHERE source_id = 'C-1'").fetchone()[
            0
        ]

    assert outcomes == ["created", "created"], "both workers believe they created the record"
    assert rows == 2, "the clinic now has the same client twice"
