"""Contract tests for POST /patients/bulk and the conflict policy (S2, S3, S4)."""

import httpx
import psycopg

OWNER = {"source_id": "C-1", "first_name": "Ana", "last_name": "Ruiz"}
OTHER_OWNER = {"source_id": "C-2", "first_name": "Luis", "last_name": "Paz"}

PATIENTS = [
    {
        "source_id": "P-1",
        "name": "Luna",
        "species": "dog",
        "date_of_birth": "2020-01-02",
        "owner_source_id": "C-1",
    },
    {"source_id": "P-2", "name": "Michi", "species": "cat", "owner_source_id": "C-1"},
    {"source_id": "P-3", "name": "Kira", "species": "dog", "owner_source_id": "C-2"},
]


def post(base_url: str, path: str, records: list[dict]) -> httpx.Response:
    return httpx.post(f"{base_url}{path}", json={"records": records}, timeout=30)


def create_owners(base_url: str) -> None:
    post(base_url, "/clients/bulk", [OWNER, OTHER_OWNER])


def count_patients(conn: psycopg.Connection) -> int:
    return conn.execute("SELECT count(*) FROM patients").fetchone()[0]


def test_bulk_create_resolves_owners_and_stores_the_link(clean_db, live_server) -> None:
    create_owners(live_server)

    response = post(live_server, "/patients/bulk", PATIENTS)

    body = response.json()
    assert response.status_code == 200
    assert [result["status"] for result in body["results"]] == ["created"] * 3
    assert body["summary"] == {"created": 3, "already_existed": 0, "conflict": 0, "failed": 0}
    assert count_patients(clean_db) == 3
    owner_id = clean_db.execute("SELECT id::text FROM clients WHERE source_id = 'C-2'").fetchone()
    linked = clean_db.execute("SELECT owner_id::text FROM patients WHERE source_id = 'P-3'")
    assert linked.fetchone()[0] == owner_id[0]


def test_a_patient_with_a_missing_owner_fails_and_the_rest_of_the_batch_goes_through(
    clean_db, live_server
) -> None:
    create_owners(live_server)
    orphan = dict(PATIENTS[0], source_id="P-404", owner_source_id="C-404")

    response = post(live_server, "/patients/bulk", [PATIENTS[0], orphan, PATIENTS[2]])

    body = response.json()
    assert [result["status"] for result in body["results"]] == ["created", "failed", "created"]
    assert body["results"][1]["error"]["code"] == "owner_not_found"
    assert body["results"][1]["id"] is None
    assert body["summary"] == {"created": 2, "already_existed": 0, "conflict": 0, "failed": 1}
    assert count_patients(clean_db) == 2


def test_resending_the_same_patient_batch_returns_the_same_ids(clean_db, live_server) -> None:
    create_owners(live_server)
    first = post(live_server, "/patients/bulk", PATIENTS).json()
    second = post(live_server, "/patients/bulk", PATIENTS).json()

    assert [result["status"] for result in second["results"]] == ["already_existed"] * 3
    assert [result["id"] for result in second["results"]] == [
        result["id"] for result in first["results"]
    ]
    assert count_patients(clean_db) == 3


def test_a_patient_resent_with_different_data_is_a_conflict(clean_db, live_server) -> None:
    create_owners(live_server)
    original = post(live_server, "/patients/bulk", PATIENTS[:1]).json()["results"][0]

    result = post(
        live_server, "/patients/bulk", [dict(PATIENTS[0], species="cat")]
    ).json()["results"][0]

    assert result["status"] == "conflict"
    assert result["id"] == original["id"]
    assert result["error"]["code"] == "payload_mismatch"
    stored = clean_db.execute("SELECT species FROM patients WHERE source_id = 'P-1'").fetchone()
    assert stored[0] == "dog"
    assert count_patients(clean_db) == 1


def test_invalid_patients_fail_per_record_and_open_no_transaction(clean_db, live_server) -> None:
    create_owners(live_server)
    records = [
        dict(PATIENTS[0], species="   "),
        dict(PATIENTS[1], date_of_birth="not-a-date"),
        dict(PATIENTS[2], name=""),
    ]

    body = post(live_server, "/patients/bulk", records).json()

    assert [result["status"] for result in body["results"]] == ["failed"] * 3
    assert [result["error"]["code"] for result in body["results"]] == ["validation_error"] * 3
    assert count_patients(clean_db) == 0


def test_validation_wins_over_a_missing_owner(clean_db, live_server) -> None:
    create_owners(live_server)
    broken = {"source_id": "P-9", "name": "", "species": "dog", "owner_source_id": "C-404"}

    result = post(live_server, "/patients/bulk", [broken]).json()["results"][0]

    assert result["status"] == "failed"
    assert result["error"]["code"] == "validation_error"
