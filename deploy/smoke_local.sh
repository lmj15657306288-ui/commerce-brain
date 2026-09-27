#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")"

if [[ -f .env ]]; then
  set -a
  # shellcheck disable=SC1091
  . ./.env
  set +a
fi

: "${COMPOSE_PROJECT_NAME:=commerce-brain-phase2b4}"
: "${POSTGRES_USER:?copy deploy/.env.example to deploy/.env and set secrets}"
: "${POSTGRES_DB:?copy deploy/.env.example to deploy/.env and set secrets}"

compose=(docker compose -p "$COMPOSE_PROJECT_NAME" -f docker-compose.yml -f docker-compose.local-test.yml --env-file .env)

"${compose[@]}" up -d --build

for attempt in $(seq 1 60); do
  if curl -fsS http://127.0.0.1:18000/readiness >/dev/null 2>&1; then
    break
  fi
  if [[ "$attempt" == "60" ]]; then
    echo "control plane did not become ready" >&2
    exit 1
  fi
  sleep 2
done

"${compose[@]}" exec -T control-plane \
  python -m control_plane.bootstrap_owner \
  --organization-id org_demo \
  --organization-name "Local Smoke Organization" \
  --actor-id actor_owner \
  --actor-role OWNER

PYTHONPATH=../bridge ../.venv/bin/python local_smoke.py
