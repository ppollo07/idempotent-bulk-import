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


def test_a_failure_in_the_middle_of_a_batch_keeps_what_was_committed(
    clean_db, live_server, monkeypatch
) -> None:
    """The tests above replace the whole use case; this one fails inside the per-record loop.

    That is the scenario the ADRs have to describe honestly: a prefix of the batch is committed,
    the response is lost, and the retry has to settle the prefix as `already_existed`.
    """
    import bulk_import.import_service as service

    real = service._write_client
    calls = {"count": 0}

    def fail_on_the_third_record(pool, index, fields, digest):
        calls["count"] += 1
        if calls["count"] == 3:
            raise psycopg.OperationalError("connection lost")
        return real(pool, index, fields, digest)

    batch = [
        {"source_id": f"C-{i}", "first_name": "Ana", "last_name": f"Ruiz {i}"} for i in range(1, 6)
    ]
    monkeypatch.setattr(service, "_write_client", fail_on_the_third_record)

    response = httpx.post(f"{live_server}/clients/bulk", json={"records": batch}, timeout=30)

    assert response.status_code == 503
    assert "partially applied" in response.json()["detail"]
    committed = clean_db.execute("SELECT source_id FROM clients ORDER BY source_id").fetchall()
    assert [row[0] for row in committed] == ["C-1", "C-2"], "the first two records did commit"

    monkeypatch.undo()
    retry = httpx.post(f"{live_server}/clients/bulk", json={"records": batch}, timeout=30)

    assert retry.status_code == 200
    assert [result["status"] for result in retry.json()["results"]] == [
        "already_existed",
        "already_existed",
        "created",
        "created",
        "created",
    ]
    assert clean_db.execute("SELECT count(*) FROM clients").fetchone()[0] == 5
