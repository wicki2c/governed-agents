#!/usr/bin/env bash
# noop smoke — the whole framework loop as one script.
#
# Why a script? Claude Code's Bash permission system matches the FULL
# command string against the allowlist. A multi-line plan like
# `PID="$(uuidgen)"; date ...; curl ...` doesn't match any single
# `Bash(uuidgen)` / `Bash(date *)` / `Bash(curl ...)` entry. Collapsing
# the workflow into one script keeps the agent's allowlist to a single
# `Bash(bash smoke.sh)` entry.
#
# `proposal_id` is server-generated; this script reads it from the POST
# response. No client-side `uuidgen`.
#
# The runtime agent (`agents/noop/CLAUDE.md`) is reduced to a single Bash
# invocation against this script. It still proves the end-to-end loop:
# this script is what the noop agent runs.

set -euo pipefail

ORCH="${ORCHESTRATOR_BASE_URL:-http://127.0.0.1:8005}"
NOW="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
# macOS / GNU date compatibility for "now + 15 minutes"
EXP="$(date -u -v+15M +%Y-%m-%dT%H:%M:%SZ 2>/dev/null \
      || date -u -d '+15 minutes' +%Y-%m-%dT%H:%M:%SZ)"

echo "noop-smoke: orchestrator=$ORCH"
echo "noop-smoke: submitting research_only proposal (server will generate proposal_id)..."

RESPONSE=$(curl -sX POST "$ORCH/proposals" \
  -H 'content-type: application/json' \
  -d "{
    \"agent_id\":\"noop\",
    \"wave\":0,
    \"submitted_at\":\"$NOW\",
    \"action_type\":\"research_only\",
    \"payload\":{\"message\":\"noop smoke\"},
    \"expected_outcome\":\"Smoke proposal approved end-to-end.\",
    \"reversible\":true,
    \"requires_approval\":true,
    \"expires_at\":\"$EXP\",
    \"rationale\":\"Framework smoke test.\"
  }")

PID=$(printf '%s' "$RESPONSE" \
      | python3 -c 'import json,sys;print(json.load(sys.stdin)["proposal_id"])')

echo "noop-smoke: proposal_id=$PID (server-generated)"
echo "noop-smoke: submitted; polling for decision..."

# Poll for up to 5 minutes (60 polls x 5s).
for i in $(seq 1 60); do
  STATUS=$(curl -s "$ORCH/proposals/$PID" | grep -o '"status":"[^"]*"' | head -1 || true)
  case "$STATUS" in
    *approved*)
      echo "noop-smoke: approved (poll $i)"
      curl -sX POST "$ORCH/audit" \
        -H 'content-type: application/json' \
        -d "{\"agent_id\":\"noop\",\"event_type\":\"action_executed\",\"proposal_id\":\"$PID\",\"payload\":{\"outcome\":\"noop\",\"smoke\":true}}" >/dev/null
      echo "noop-smoke: posted action_executed"
      curl -sX POST "$ORCH/audit" \
        -H 'content-type: application/json' \
        -d "{\"agent_id\":\"noop\",\"event_type\":\"agent_stop\",\"proposal_id\":\"$PID\",\"payload\":{\"reason\":\"task_complete\"}}" >/dev/null
      echo "noop-smoke: posted agent_stop; exiting 0"
      printf '\n[%s] proposal %s approved, action_executed posted, run complete\n' \
        "$(date -u +%FT%TZ)" "$PID" >> memory.md
      exit 0
      ;;
    *rejected*)
      echo "noop-smoke: rejected; exiting 0"
      curl -sX POST "$ORCH/audit" \
        -H 'content-type: application/json' \
        -d "{\"agent_id\":\"noop\",\"event_type\":\"agent_stop\",\"proposal_id\":\"$PID\",\"payload\":{\"reason\":\"proposal_rejected\"}}" >/dev/null
      printf '\n[%s] proposal %s rejected\n' "$(date -u +%FT%TZ)" "$PID" >> memory.md
      exit 0
      ;;
    *)
      sleep 5
      ;;
  esac
done

echo "noop-smoke: timed out waiting for decision"
curl -sX POST "$ORCH/audit" \
  -H 'content-type: application/json' \
  -d "{\"agent_id\":\"noop\",\"event_type\":\"agent_stop\",\"proposal_id\":\"$PID\",\"payload\":{\"reason\":\"poll_timeout\"}}" >/dev/null
printf '\n[%s] proposal %s timed out (no decision in 5 min)\n' "$(date -u +%FT%TZ)" "$PID" >> memory.md
exit 1
