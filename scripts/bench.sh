#!/usr/bin/env bash
# Reproduces the throughput numbers in the README: 1000 records, first load and a replay.
#
#   docker compose up -d
#   ./scripts/bench.sh
set -euo pipefail

compose() {
  if docker compose version >/dev/null 2>&1; then docker compose "$@"; else docker-compose "$@"; fi
}

compose exec -T app python - > /tmp/bench-1000.json <<'PY'
import json

print(json.dumps({"records": [
    {"source_id": f"BENCH-{i}", "first_name": "Ana", "last_name": f"Ruiz {i}"} for i in range(1000)
]}))
PY

post() {
  curl -s -o /dev/null -w '%{http_code}\n' -X POST localhost:8000/clients/bulk \
    -H 'content-type: application/json' --data-binary @/tmp/bench-1000.json
}

echo "first load of 1000 records:"
time post
echo "the same batch again:"
time post

echo
echo "Clean up with: delete from clients where source_id like 'BENCH-%'"
