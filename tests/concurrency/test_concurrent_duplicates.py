"""Concurrency tests (S3, S5).

These run against a real uvicorn server on a real socket, with one connection per request.
``TestClient`` calls the ASGI app in-process, so it cannot prove anything about two workers
racing against the same database: the race is in PostgreSQL, and the point of the test is
that only the UNIQUE constraint closes it.

Each test repeats a few rounds, because a race that did not happen once proves nothing.
"""

import threading
from concurrent.futures import ThreadPoolExecutor

import httpx
import psycopg
import pytest

SENDERS = 10
ROUNDS = 5

CLIENTS = [
    {"source_id": "C-1", "first_name": "Ana", "last_name": "Ruiz"},
    {"source_id": "C-2", "first_name": "Luis", "last_name": "Paz"},
]


def send_at_once(base_url: str, path: str, payloads: list[list[dict]]) -> list[httpx.Response]:
    """Fire every payload at the same instant, each from its own thread."""
    barrier = threading.Barrier(len(payloads))

    def send(payload: list[dict]) -> httpx.Response:
        barrier.wait(timeout=30)
        return httpx.post(f"{base_url}{path}", json={"records": payload}, timeout=60)

    with ThreadPoolExecutor(max_workers=len(payloads)) as executor:
        futures = [executor.submit(send, payload) for payload in payloads]
        return [future.result() for future in futures]


@pytest.mark.parametrize("round_number", range(ROUNDS))
def test_the_same_batch_sent_by_ten_workers_creates_exactly_one_row(
    clean_db: psycopg.Connection, live_server: str, round_number: int
) -> None:
    responses = send_at_once(live_server, "/clients/bulk", [CLIENTS] * SENDERS)

    assert [response.status_code for response in responses] == [200] * SENDERS
    bodies = [response.json() for response in responses]

    for position in range(len(CLIENTS)):
        statuses = [body["results"][position]["status"] for body in bodies]
        ids = {body["results"][position]["id"] for body in bodies}

        assert statuses.count("created") == 1, statuses
        assert set(statuses) <= {"created", "already_existed"}, statuses
        assert len(ids) == 1, ids
        assert None not in ids

    assert clean_db.execute("SELECT count(*) FROM clients").fetchone()[0] == len(CLIENTS)


def test_overlapping_batches_in_reverse_order_do_not_duplicate_or_deadlock(
    clean_db: psycopg.Connection, live_server: str
) -> None:
    shared = [
        {"source_id": f"S-{index}", "first_name": "Ana", "last_name": f"Ruiz {index}"}
        for index in range(4)
    ]
    forward = shared + [{"source_id": "F-1", "first_name": "Luis", "last_name": "Paz"}]
    backward = list(reversed(shared)) + [
        {"source_id": "B-1", "first_name": "Marta", "last_name": "Gil"}
    ]

    responses = send_at_once(live_server, "/clients/bulk", [forward, backward])

    assert [response.status_code for response in responses] == [200, 200]
    created = [
        result["source_id"]
        for response in responses
        for result in response.json()["results"]
        if result["status"] == "created"
    ]
    assert sorted(created) == sorted({record["source_id"] for record in forward + backward})
    assert clean_db.execute("SELECT count(*) FROM clients").fetchone()[0] == 6


def test_concurrent_patients_with_the_same_source_id_create_one_row(
    clean_db: psycopg.Connection, live_server: str
) -> None:
    owner = {"source_id": "C-1", "first_name": "Ana", "last_name": "Ruiz"}
    httpx.post(f"{live_server}/clients/bulk", json={"records": [owner]}, timeout=30)
    patient = {
        "source_id": "P-1",
        "name": "Luna",
        "species": "dog",
        "date_of_birth": "2020-01-02",
        "owner_source_id": "C-1",
    }

    responses = send_at_once(live_server, "/patients/bulk", [[patient]] * SENDERS)

    assert [response.status_code for response in responses] == [200] * SENDERS
    statuses = [response.json()["results"][0]["status"] for response in responses]
    ids = {response.json()["results"][0]["id"] for response in responses}

    assert statuses.count("created") == 1, statuses
    assert len(ids) == 1
    assert clean_db.execute("SELECT count(*) FROM patients").fetchone()[0] == 1
