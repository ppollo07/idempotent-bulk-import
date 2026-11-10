"""The files in `examples/` are documentation, so this test keeps them true.

They are posted in the same order as `scripts/demo.sh`, and every outcome the README documents is
asserted here. If someone changes an example without changing the behaviour, or the other way
around, this fails: that is the whole point of having fixtures instead of prose.
"""

import json
from pathlib import Path

import httpx
import psycopg

EXAMPLES = Path(__file__).resolve().parents[2] / "examples"


def payload(name: str) -> dict:
    return json.loads((EXAMPLES / name).read_text(encoding="utf-8"))


def post(base_url: str, endpoint: str, name: str) -> dict:
    response = httpx.post(f"{base_url}{endpoint}", json=payload(name), timeout=30)
    assert response.status_code == 200, response.text
    return response.json()


def test_the_documented_scenario_holds(clean_db: psycopg.Connection, live_server: str) -> None:
    created = post(live_server, "/clients/bulk", "clients-batch.json")
    assert created["summary"] == {"created": 2, "already_existed": 0, "conflict": 0, "failed": 0}

    cosmetic = post(live_server, "/clients/bulk", "clients-cosmetic-retry.json")
    assert cosmetic["summary"] == {
        "created": 0,
        "already_existed": 2,
        "conflict": 0,
        "failed": 0,
    }
    assert [result["id"] for result in cosmetic["results"]] == [
        result["id"] for result in created["results"]
    ]

    changed = post(live_server, "/clients/bulk", "clients-changed.json")
    assert changed["summary"]["conflict"] == 1
    assert changed["results"][0]["error"]["code"] == "payload_mismatch"
    assert changed["results"][0]["id"] == created["results"][0]["id"]

    mixed = post(live_server, "/clients/bulk", "clients-mixed.json")
    assert [result["status"] for result in mixed["results"]] == [
        "created",
        "failed",
        "already_existed",
    ]
    assert mixed["results"][1]["error"]["code"] == "validation_error"

    patients = post(live_server, "/patients/bulk", "patients-batch.json")
    assert patients["summary"] == {"created": 1, "already_existed": 0, "conflict": 0, "failed": 1}
    assert patients["results"][1]["error"]["code"] == "owner_not_found"

    assert clean_db.execute("SELECT count(*) FROM clients").fetchone()[0] == 3
    assert clean_db.execute("SELECT count(*) FROM patients").fetchone()[0] == 1
