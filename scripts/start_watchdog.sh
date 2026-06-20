#!/usr/bin/env bash
# Start the watchdog as a standalone process. It opens its own SQLModel
# session against infra/db.sqlite (WAL mode handles the concurrent
# reader/writer with the orchestrator).
#
# Sources .env.local if present, for per-deployment env vars like
# WATCHDOG_INTERVAL_SECONDS.
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

echo "starting watchdog (polling every ${WATCHDOG_INTERVAL_SECONDS:-60}s)"
exec uv run python -m infra.watchdog
