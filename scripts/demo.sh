#!/usr/bin/env bash
# A scripted migration against a running stack, in the order a real run would happen:
# a full load, a cosmetic retry, a changed record, a mixed batch, then patients.
#
#   docker compose up -d
#   ./scripts/demo.sh
#
# The report comes from the service's own answers, not from a mock.
set -euo pipefail

# `docker compose` where the plugin exists, the standalone binary where it does not.
compose() {
  if docker compose version >/dev/null 2>&1; then
    docker compose "$@"
  else
    docker-compose "$@"
  fi
}

# `docker compose up -d` returns when the container starts, not when the app answers.
wait_for_service() {
  local url="${READY_URL:-http://localhost:8000/health}"
  printf 'waiting for %s' "$url"
  for _ in $(seq 1 "${READY_ATTEMPTS:-30}"); do
    if curl -fsS "$url" >/dev/null 2>&1; then
      echo
      return 0
    fi
    printf '.'
    sleep 1
  done
  echo
  echo "the service is not answering at $url" >&2
  echo "start it first:  docker compose up -d" >&2
  exit 1
}

report() {
  compose exec -T app python /app/scripts/report.py "$1" "$2"
}

wait_for_service

report examples/clients-batch.json /clients/bulk
report examples/clients-cosmetic-retry.json /clients/bulk
report examples/clients-changed.json /clients/bulk
report examples/clients-mixed.json /clients/bulk
report examples/patients-batch.json /patients/bulk

echo
echo "Totals in the database:"
compose exec -T db psql -U postgres -d bulk_import -c \
  "select 'clients' as table, count(*) from clients union all select 'patients', count(*) from patients"
