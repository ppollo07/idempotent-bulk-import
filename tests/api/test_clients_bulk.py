"""Contract tests for POST /clients/bulk (S1, S2, S3, S4)."""

import httpx
import psycopg
import pytest

CLIENTS = [
    {"source_id": "C-1", "first_name": "Ana", "last_name": "Ruiz", "email": "ana@example.com"},
    {"source_id": "C-2", "first_name": "Luis", "last_name": "Paz"},
    {"source_id": "C-3", "first_name": "Marta", "last_name": "Gil", "email": "marta@example.com"},
]


def post(base_url: str, records: list[dict]) -> httpx.Response:
    return httpx.post(f"{base_url}/clients/bulk", json={"records": records}, timeout=30)


def count_clients(conn: psycopg.Connection) -> int:
    return conn.execute("SELECT count(*) FROM clients").fetchone()[0]


def test_bulk_create_returns_one_result_per_record(clean_db, live_server) -> None:
    response = post(live_server, CLIENTS)

    assert response.status_code == 200
    body = response.json()
    assert body["summary"] == {"created": 3, "already_existed": 0, "conflict": 0, "failed": 0}
    assert [result["index"] for result in body["results"]] == [0, 1, 2]
    assert [result["source_id"] for result in body["results"]] == ["C-1", "C-2", "C-3"]
    assert [result["status"] for result in body["results"]] == ["created"] * 3
    assert all(result["id"] and result["error"] is None for result in body["results"])
    assert count_clients(clean_db) == 3


def test_resending_the_same_batch_is_safe_and_returns_the_same_ids(clean_db, live_server) -> None:
    first = post(live_server, CLIENTS).json()
    second = post(live_server, CLIENTS).json()

    assert [result["status"] for result in second["results"]] == ["already_existed"] * 3
    assert [result["id"] for result in second["results"]] == [
        result["id"] for result in first["results"]
    ]
    assert second["summary"] == {"created": 0, "already_existed": 3, "conflict": 0, "failed": 0}
    assert count_clients(clean_db) == 3


def test_a_record_resent_with_different_data_is_a_conflict_and_is_not_overwritten(
    clean_db, live_server
) -> None:
    original = post(live_server, CLIENTS[:1]).json()["results"][0]

    changed = dict(CLIENTS[0], last_name="Ruiz Gil")
    response = post(live_server, [changed])

    result = response.json()["results"][0]
    assert result["status"] == "conflict"
    assert result["id"] == original["id"]
    assert result["error"]["code"] == "payload_mismatch"
    stored = clean_db.execute("SELECT last_name FROM clients WHERE source_id = 'C-1'").fetchone()
    assert stored[0] == "Ruiz"
    assert count_clients(clean_db) == 1


def test_a_record_resent_with_cosmetic_differences_is_not_a_conflict(clean_db, live_server) -> None:
    post(live_server, CLIENTS[:1])

    cosmetic = dict(CLIENTS[0], first_name="  Ana ", email=" ANA@Example.com ")
    result = post(live_server, [cosmetic]).json()["results"][0]

    assert result["status"] == "already_existed"
    assert count_clients(clean_db) == 1


def test_duplicate_source_id_inside_one_request(clean_db, live_server) -> None:
    records = [CLIENTS[0], dict(CLIENTS[0], last_name="Otro")]

    response = post(live_server, records)

    first, second = response.json()["results"]
    assert first["status"] == "created"
    assert second["status"] == "conflict"
    assert second["id"] == first["id"]
    assert count_clients(clean_db) == 1


def test_failed_records_do_not_affect_the_rest_of_the_batch(clean_db, live_server) -> None:
    invalid = {"source_id": "C-9", "first_name": "", "last_name": "Sin nombre"}
    records = [CLIENTS[0], invalid, CLIENTS[1]]

    response = post(live_server, records)

    body = response.json()
    assert response.status_code == 200
    assert body["results"][0]["status"] == "created"
    assert body["results"][1]["status"] == "failed"
    assert body["results"][1]["error"]["code"] == "validation_error"
    assert body["results"][1]["id"] is None
    assert body["results"][2]["status"] == "created"
    assert body["summary"] == {"created": 2, "already_existed": 0, "conflict": 0, "failed": 1}
    assert count_clients(clean_db) == 2


def test_envelope_errors_are_422(clean_db, live_server) -> None:
    empty = post(live_server, [])
    too_many = post(live_server, [dict(CLIENTS[0], source_id=f"C-{i}") for i in range(1001)])
    wrong_shape = httpx.post(f"{live_server}/clients/bulk", json={"records": "C-1"}, timeout=30)

    assert empty.status_code == 422
    assert too_many.status_code == 422
    assert wrong_shape.status_code == 422
    assert count_clients(clean_db) == 0


def test_the_unique_constraint_rejects_a_second_row_with_the_same_source_id(
    clean_db, live_server
) -> None:
    """The constraint the whole design rests on, asserted directly."""
    clean_db.execute(
        "INSERT INTO clients (source_id, first_name, last_name, payload_hash) "
        "VALUES ('C-1', 'Ana', 'Ruiz', 'digest')"
    )
    clean_db.commit()

    with pytest.raises(psycopg.errors.UniqueViolation):
        clean_db.execute(
            "INSERT INTO clients (source_id, first_name, last_name, payload_hash) "
            "VALUES ('C-1', 'Ana', 'Ruiz', 'digest')"
        )
    clean_db.rollback()

    assert count_clients(clean_db) == 1


def test_a_control_character_fails_only_that_record(clean_db, live_server) -> None:
    """A NUL byte or another control character is rejected, never silently stripped."""
    nul = {"source_id": "C-9", "first_name": "a\u0000b", "last_name": "Roto"}
    other = {"source_id": "C-10", "first_name": "a\x01b", "last_name": "Roto"}

    response = post(live_server, [CLIENTS[0], nul, other, CLIENTS[1]])

    assert response.status_code == 200, response.text
    body = response.json()
    assert [result["status"] for result in body["results"]] == [
        "created",
        "failed",
        "failed",
        "created",
    ]
    assert [result["error"]["code"] for result in body["results"][1:3]] == [
        "validation_error",
        "validation_error",
    ]
    assert "control character" in body["results"][1]["error"]["message"]
    assert count_clients(clean_db) == 2


def test_an_element_that_is_not_an_object_rejects_the_envelope(clean_db, live_server) -> None:
    """The one boundary where the batch itself is rejected.

    A per-record outcome needs a record to report on. An element that is not an object cannot be
    reported per record, so it is an envelope error — and the README says so.
    """
    for bad_element in (None, "C-1", 42):
        response = post(live_server, [CLIENTS[0], bad_element])

        assert response.status_code == 422, bad_element

    assert count_clients(clean_db) == 0
