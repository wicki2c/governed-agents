#!/usr/bin/env bash
# publisher example — the custom decision-hook loop as one script.
#
# Submits a `publish_note` proposal, polls for the human decision, and on
# approval records `action_executed`. The publish side effect (writing the
# note file) is performed server-side by the decision hook in
# agents/publisher/hooks.py — NOT by this script.
#
# Collapsed into one script for the same reason as noop/smoke.sh: Claude
# Code's Bash permission matches the FULL command string, so one
# `Bash(bash publish.sh)` entry authorises the whole flow.
#
# NOTE: for the hook to fire against a running orchestrator, that orchestrator
# must have imported agents.publisher.hooks at startup (see README.md,
# "Wiring it in"). The end-to-end test imports it in-process.

set -euo pipefail

ORCH="${ORCHESTRATOR_BASE_URL:-http://127.0.0.1:8005}"
SLUG="${PUBLISH_SLUG:-hello-world}"
BODY="${PUBLISH_BODY:-# Hello\nFrom a governed agent.}"
NOW="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
EXP="$(date -u -v+15M +%Y-%m-%dT%H:%M:%SZ 2>/dev/null \
      || date -u -d '+15 minutes' +%Y-%m-%dT%H:%M:%SZ)"

echo "publisher: orchestrator=$ORCH"
echo "publisher: submitting publish_note proposal (slug=$SLUG)..."

RESPONSE=$(curl -sX POST "$ORCH/proposals" \
  -H 'content-type: application/json' \
  -d "{
    \"agent_id\":\"publisher\",
    \"wave\":0,
    \"submitted_at\":\"$NOW\",
    \"action_type\":\"publish_note\",
    \"payload\":{\"slug\":\"$SLUG\",\"body\":\"$BODY\"},
    \"expected_outcome\":\"Publish a note once a human approves.\",
    \"reversible\":true,
    \"requires_approval\":true,
    \"expires_at\":\"$EXP\",
    \"rationale\":\"Demo of a custom post-approval decision hook.\"
  }")

PID=$(printf '%s' "$RESPONSE" \
      | python3 -c 'import json,sys;print(json.load(sys.stdin)["proposal_id"])')

echo "publisher: proposal_id=$PID; polling for decision..."

for i in $(seq 1 60); do  # up to ~5 minutes
  STATUS=$(curl -s "$ORCH/proposals/$PID" | grep -o '"status":"[^"]*"' | head -1 || true)
  case "$STATUS" in
    *approved*)
      echo "publisher: approved (poll $i) — hook published the note server-side"
      curl -sX POST "$ORCH/audit" -H 'content-type: application/json' \
        -d "{\"agent_id\":\"publisher\",\"event_type\":\"action_executed\",\"proposal_id\":\"$PID\",\"payload\":{\"slug\":\"$SLUG\"}}" >/dev/null
      curl -sX POST "$ORCH/audit" -H 'content-type: application/json' \
        -d "{\"agent_id\":\"publisher\",\"event_type\":\"agent_stop\",\"proposal_id\":\"$PID\",\"payload\":{\"reason\":\"task_complete\"}}" >/dev/null
      printf '\n[%s] proposal %s approved, note %s published\n' "$(date -u +%FT%TZ)" "$PID" "$SLUG" >> memory.md
      exit 0
      ;;
    *rejected*)
      echo "publisher: rejected — gate held, nothing published; exiting 0"
      curl -sX POST "$ORCH/audit" -H 'content-type: application/json' \
        -d "{\"agent_id\":\"publisher\",\"event_type\":\"agent_stop\",\"proposal_id\":\"$PID\",\"payload\":{\"reason\":\"proposal_rejected\"}}" >/dev/null
      printf '\n[%s] proposal %s rejected\n' "$(date -u +%FT%TZ)" "$PID" >> memory.md
      exit 0
      ;;
    *)
      sleep 5
      ;;
  esac
done

echo "publisher: timed out waiting for decision"
curl -sX POST "$ORCH/audit" -H 'content-type: application/json' \
  -d "{\"agent_id\":\"publisher\",\"event_type\":\"agent_stop\",\"proposal_id\":\"$PID\",\"payload\":{\"reason\":\"poll_timeout\"}}" >/dev/null
exit 1
