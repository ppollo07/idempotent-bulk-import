"""When the connection is the problem, the answer is 503, not a per-record failure.

Telling the caller "this record failed" for a database that is down is a lie: the record was never
attempted. This is the other half of the distinction in import_service: DataError and
IntegrityError belong to the record; OperationalError and a pool timeout belong to the connection.
"""

import httpx
import psycopg

import bulk_import.main as main

CLIENT = {"source_id": "C-1", "first_name": "Ana", "last_name": "Ruiz"}


def test_a_connection_failure_answers_503_and_reports_no_per_record_result(
    clean_db, live_server, monkeypatch
) -> None:
    def explode(pool, records):
        raise psycopg.OperationalError("connection lost")

    monkeypatch.setattr(main, "import_clients", explode)

    response = httpx.post(f"{live_server}/clients/bulk", json={"records": [CLIENT]}, timeout=30)

    assert response.status_code == 503
    body = response.json()
    assert "retry the batch" in body["detail"]
    assert "results" not in body
    assert clean_db.execute("SELECT count(*) FROM clients").fetchone()[0] == 0


def test_a_pool_timeout_is_a_503_too(clean_db, live_server, monkeypatch) -> None:
    from psycopg_pool import PoolTimeout

    def explode(pool, records):
        raise PoolTimeout("couldn't get a connection after 30.00 sec")

    monkeypatch.setattr(main, "import_patients", explode)

    response = httpx.post(
        f"{live_server}/patients/bulk",
        json={
            "records": [
                {
                    "source_id": "P-1",
                    "name": "Luna",
                    "species": "dog",
                    "owner_source_id": "C-1",
                }
            ]
        },
        timeout=30,
    )

    assert response.status_code == 503
