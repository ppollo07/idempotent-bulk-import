"""Print a small report of one bulk import request: the counts, then a line per record.

Standard library only, so it runs inside the service image without adding a dependency:

    docker compose exec -T app python /app/scripts/report.py \
        examples/clients-batch.json /clients/bulk

Use `scripts/demo.sh` to run the whole scripted migration in order.
"""

import json
import os
import sys
import urllib.error
import urllib.request

BASE_URL = os.environ.get("BULK_IMPORT_URL", "http://localhost:8000")
STATUSES = ("created", "already_existed", "conflict", "failed")
MARK = {"created": "█", "already_existed": "·", "conflict": "!", "failed": "x"}


def post(endpoint: str, payload: dict) -> tuple[int, dict]:
    request = urllib.request.Request(
        BASE_URL + endpoint,
        data=json.dumps(payload).encode("utf-8"),
        headers={"content-type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return response.status, json.load(response)
    except urllib.error.HTTPError as error:
        return error.code, json.load(error)
    except urllib.error.URLError as error:
        raise SystemExit(
            f"cannot reach {BASE_URL} ({error.reason}).\n"
            "is the service running?  docker compose up -d"
        ) from None


def main() -> None:
    path, endpoint = sys.argv[1], sys.argv[2]
    with open(path, encoding="utf-8") as handle:
        payload = json.load(handle)

    status, body = post(endpoint, payload)
    print(f"\n{status}  POST {endpoint}   <- {path}")

    if "summary" not in body:
        print(json.dumps(body, indent=2))
        return

    summary = body["summary"]
    count = len(body["results"])
    print(f"    {count} {'record' if count == 1 else 'records'} in the batch")
    for name in STATUSES:
        count = summary[name]
        print(f"    {name:<16}{count:>3}  {MARK[name] * count}")
    print()
    for result in body["results"]:
        error = result["error"]["code"] if result["error"] else ""
        record_id = result["id"][:8] if result["id"] else "-"
        source_id = result["source_id"] or "-"
        line = f"    #{result['index']}  {source_id:<6} {result['status']:<16}"
        print(f"{line} id={record_id:<9} {error}")


if __name__ == "__main__":
    main()
