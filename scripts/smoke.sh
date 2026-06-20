#!/usr/bin/env bash
# Orchestrator health probe. Boots the server, polls /status until 200,
# hits a couple of read-only endpoints, then tears down. Treats any
# non-200 as failure.
set -euo pipefail

PORT="${ORCHESTRATOR_PORT:-8005}"
BASE="http://127.0.0.1:${PORT}"
LOG=$(mktemp -t orchestrator-smoke.XXXXXX.log)

cleanup() {
  if [[ -n "${PID:-}" ]] && kill -0 "${PID}" 2>/dev/null; then
    kill "${PID}" 2>/dev/null || true
    wait "${PID}" 2>/dev/null || true
  fi
  rm -f "${LOG}"
}
trap cleanup EXIT

echo "smoke: booting orchestrator on ${BASE} (log: ${LOG})"
ORCHESTRATOR_PORT="${PORT}" uv run uvicorn infra.app:app \
  --host 127.0.0.1 --port "${PORT}" \
  >"${LOG}" 2>&1 &
PID=$!

# Wait up to 15s for /status to return 200.
for _ in $(seq 1 30); do
  if curl -fsS "${BASE}/status" >/dev/null 2>&1; then
    break
  fi
  sleep 0.5
done

if ! curl -fsS "${BASE}/status" >/dev/null; then
  echo "smoke: FAIL — /status never came up. Log tail:"
  tail -50 "${LOG}"
  exit 1
fi

echo "smoke: /status responding"
curl -fsS "${BASE}/status" | python3 -c "import sys,json; d=json.load(sys.stdin); assert d['ok'] is True, d; assert d['host']=='127.0.0.1', d; print('  status.ok=True host=127.0.0.1 paused='+str(d['agents_paused']))"

echo "smoke: /scoreboard responding"
curl -fsS "${BASE}/scoreboard" | python3 -c "import sys,json; d=json.load(sys.stdin); assert isinstance(d, list); print(f'  scoreboard rows: {len(d)} agents')"

echo "smoke: / returns dashboard HTML"
curl -fsS "${BASE}/" | grep -q "Governed Agents" || { echo "smoke: FAIL — dashboard HTML missing title"; exit 1; }

echo "smoke: lsof bind check"
lsof -nP -iTCP:"${PORT}" -sTCP:LISTEN | awk 'NR>1 {print "  "$0}' | head -3
if ! lsof -nP -iTCP:"${PORT}" -sTCP:LISTEN | grep -q "127\.0\.0\.1:${PORT}"; then
  echo "smoke: FAIL — bind is not 127.0.0.1"
  exit 1
fi

echo "smoke: PASS"
