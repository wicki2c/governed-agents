#!/usr/bin/env bash
# Start the FastAPI orchestration server on 127.0.0.1 only.
# Honours ORCHESTRATOR_PORT (default 8005). --reload is handy for local
# dev; drop it for a long-running process.
#
# Sources .env.local (gitignored) if present, so any per-deployment env
# vars reach the orchestrator process. Secrets live in .env.local only,
# never in the repo.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
ENV_FILE="${REPO_ROOT}/.env.local"

if [[ -f "${ENV_FILE}" ]]; then
    set -a
    # shellcheck disable=SC1090
    source "${ENV_FILE}"
    set +a
    echo "sourced ${ENV_FILE}"
fi

HOST="127.0.0.1"
PORT="${ORCHESTRATOR_PORT:-8005}"

echo "starting orchestrator on http://${HOST}:${PORT}"
exec uv run uvicorn infra.app:app --host "${HOST}" --port "${PORT}" --reload
