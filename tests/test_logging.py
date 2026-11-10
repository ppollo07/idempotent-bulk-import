"""The log is for the operator, the response is for the caller.

A migration tool sees its own response. Whoever has to answer "where did those 300 patients go?"
a week later has only the log, so the two things that matter there are asserted here: the batch
summary, and one line per record that was rejected with the reason.
"""

import httpx

CLIENT = {"source_id": "C-1", "first_name": "Ana", "last_name": "Ruiz"}
PATIENT = {
    "source_id": "P-1",
    "name": "Luna",
    "species": "dog",
    "owner_source_id": "C-404",
}


def test_a_batch_logs_its_summary_and_every_rejected_record(clean_db, live_server, caplog) -> None:
    with caplog.at_level("INFO", logger="bulk_import"):
        httpx.post(f"{live_server}/clients/bulk", json={"records": [CLIENT]}, timeout=30)
        httpx.post(
            f"{live_server}/clients/bulk",
            json={"records": [dict(CLIENT, last_name="Ruiz Gil")]},
            timeout=30,
        )
        httpx.post(f"{live_server}/patients/bulk", json={"records": [PATIENT]}, timeout=30)

    messages = [record.getMessage() for record in caplog.records]

    assert any(
        "clients batch of 1" in message and "'created': 1" in message for message in messages
    )
    assert any(
        "clients batch of 1" in message and "'conflict': 1" in message for message in messages
    )
    assert any("clients record C-1: payload_mismatch" in message for message in messages), messages
    assert any("patients record P-1: owner_not_found" in message for message in messages), messages
    assert any(
        "patients batch of 1" in message and "'failed': 1" in message for message in messages
    )
