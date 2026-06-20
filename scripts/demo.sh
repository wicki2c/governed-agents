#!/usr/bin/env bash
# Zero-LLM demo of the governance loop. Boots the orchestrator, submits a
# proposal for an EXTERNAL action (which the policy forces to require human
# approval), and waits for you to approve or reject it on the dashboard —
# then prints the resulting audit chain. No Anthropic key or paid layer.
set -euo pipefail

PORT="${ORCHESTRATOR_PORT:-8005}"
BASE="http://127.0.0.1:${PORT}"
AGENT="demo-agent"
LOG="$(mktemp -t governed-demo.XXXXXX.log)"

cleanup() {
  if [[ -n "${PID:-}" ]] && kill -0 "${PID}" 2>/dev/null; then
    kill "${PID}" 2>/dev/null || true
    wait "${PID}" 2>/dev/null || true
  fi
  rm -f "${LOG}"
}
trap cleanup EXIT

echo "demo: booting orchestrator on ${BASE} (log: ${LOG})"
ORCHESTRATOR_PORT="${PORT}" uv run uvicorn infra.app:app \
  --host 127.0.0.1 --port "${PORT}" >"${LOG}" 2>&1 &
PID=$!

for _ in $(seq 1 40); do
  curl -fsS "${BASE}/status" >/dev/null 2>&1 && break
  sleep 0.5
done
if ! curl -fsS "${BASE}/status" >/dev/null 2>&1; then
  echo "demo: FAIL — orchestrator did not start. Log tail:"; tail -30 "${LOG}"; exit 1
fi

NOW="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
EXP="$(date -u -v+30M +%Y-%m-%dT%H:%M:%SZ 2>/dev/null \
      || date -u -d '+30 minutes' +%Y-%m-%dT%H:%M:%SZ)"

echo "demo: submitting a 'publish_content' proposal — an external action the"
echo "      policy forces to require human approval..."
RESPONSE=$(curl -sX POST "${BASE}/proposals" -H 'content-type: application/json' -d "{
  \"agent_id\":\"${AGENT}\",
  \"wave\":0,
  \"submitted_at\":\"${NOW}\",
  \"action_type\":\"publish_content\",
  \"payload\":{\"title\":\"Hello from a governed agent\",\"url\":\"https://example.com/post\"},
  \"expected_outcome\":\"Publish a post to the public site.\",
  \"reversible\":false,
  \"requires_approval\":true,
  \"expires_at\":\"${EXP}\",
  \"rationale\":\"Demo of the proposal-gate: this action needs a human.\"
}")
PROP=$(printf '%s' "${RESPONSE}" \
      | python3 -c 'import json,sys;print(json.load(sys.stdin)["proposal_id"])')

echo
echo "  ✋  Proposal ${PROP} is PENDING."
echo "      Open ${BASE} in your browser and APPROVE or REJECT it."
echo "      (The agent cannot proceed until you decide — that is the point.)"
echo

DECISION="pending"
for _ in $(seq 1 150); do  # up to ~5 minutes
  STATUS=$(curl -s "${BASE}/proposals/${PROP}" \
    | python3 -c 'import json,sys;print(json.load(sys.stdin).get("status","pending"))' 2>/dev/null || echo "pending")
  if [[ "${STATUS}" != "pending" ]]; then DECISION="${STATUS}"; break; fi
  sleep 2
done

echo "demo: proposal status is now: ${DECISION}"
if [[ "${DECISION}" == "approved" ]]; then
  # The agent would now perform the action and record that it did.
  curl -sX POST "${BASE}/audit" -H 'content-type: application/json' \
    -d "{\"agent_id\":\"${AGENT}\",\"event_type\":\"action_executed\",\"proposal_id\":\"${PROP}\",\"payload\":{\"published\":true}}" >/dev/null
  echo "demo: action_executed recorded."
fi

echo
echo "demo: audit chain for ${AGENT} (oldest first):"
curl -s "${BASE}/audit?agent_id=${AGENT}&limit=50" | python3 -c '
import json, sys
for r in reversed(json.load(sys.stdin)):
    print(f"  {r[\"ts\"]}  {r[\"event_type\"]}")
'
echo
echo "demo: done — the orchestrator will now shut down."
