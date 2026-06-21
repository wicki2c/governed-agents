#!/usr/bin/env bash
# notifier example — the custom decision-hook loop as one script.
#
# Submits a `notify_on_approval` proposal, polls for the human decision, and
# on approval records `action_executed`. The notification side effect
# (appending a line to notifications.log) is performed server-side by the
# decision hook in agents/notifier/hooks.py — NOT by this script.
#
# Collapsed into one script for the same reason as noop/smoke.sh: Claude
# Code's Bash permission matches the FULL command string, so one
# `Bash(bash notify.sh)` entry authorises the whole flow.
#
# NOTE: for the hook to fire against a running orchestrator, that orchestrator
# must have imported agents.notifier.hooks at startup (see README.md,
# "Wiring it in"). The end-to-end test imports it in-process.

set -euo pipefail

ORCH="${ORCHESTRATOR_BASE_URL:-http://127.0.0.1:8005}"
MESSAGE="${NOTIFY_MESSAGE:-A governed action was approved.}"
NOW="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
EXP="$(date -u -v+15M +%Y-%m-%dT%H:%M:%SZ 2>/dev/null \
      || date -u -d '+15 minutes' +%Y-%m-%dT%H:%M:%SZ)"

echo "notifier: orchestrator=$ORCH"
echo "notifier: submitting notify_on_approval proposal..."

RESPONSE=$(curl -sX POST "$ORCH/proposals" \
  -H 'content-type: application/json' \
  -d "{
    \"agent_id\":\"notifier\",
    \"wave\":0,
    \"submitted_at\":\"$NOW\",
    \"action_type\":\"notify_on_approval\",
    \"payload\":{\"message\":\"$MESSAGE\"},
    \"expected_outcome\":\"Emit a notification once a human approves.\",
    \"reversible\":true,
    \"requires_approval\":true,
    \"expires_at\":\"$EXP\",
    \"rationale\":\"Demo of a custom post-approval decision hook.\"
  }")

PID=$(printf '%s' "$RESPONSE" \
      | python3 -c 'import json,sys;print(json.load(sys.stdin)["proposal_id"])')

echo "notifier: proposal_id=$PID; polling for decision..."

for i in $(seq 1 60); do  # up to ~5 minutes
  STATUS=$(curl -s "$ORCH/proposals/$PID" | grep -o '"status":"[^"]*"' | head -1 || true)
  case "$STATUS" in
    *approved*)
      echo "notifier: approved (poll $i) — hook appended the notification server-side"
      curl -sX POST "$ORCH/audit" -H 'content-type: application/json' \
        -d "{\"agent_id\":\"notifier\",\"event_type\":\"action_executed\",\"proposal_id\":\"$PID\",\"payload\":{\"message\":\"$MESSAGE\"}}" >/dev/null
      curl -sX POST "$ORCH/audit" -H 'content-type: application/json' \
        -d "{\"agent_id\":\"notifier\",\"event_type\":\"agent_stop\",\"proposal_id\":\"$PID\",\"payload\":{\"reason\":\"task_complete\"}}" >/dev/null
      printf '\n[%s] proposal %s approved, notification emitted\n' "$(date -u +%FT%TZ)" "$PID" >> memory.md
      exit 0
      ;;
    *rejected*)
      echo "notifier: rejected — gate held, nothing emitted; exiting 0"
      curl -sX POST "$ORCH/audit" -H 'content-type: application/json' \
        -d "{\"agent_id\":\"notifier\",\"event_type\":\"agent_stop\",\"proposal_id\":\"$PID\",\"payload\":{\"reason\":\"proposal_rejected\"}}" >/dev/null
      printf '\n[%s] proposal %s rejected\n' "$(date -u +%FT%TZ)" "$PID" >> memory.md
      exit 0
      ;;
    *)
      sleep 5
      ;;
  esac
done

echo "notifier: timed out waiting for decision"
curl -sX POST "$ORCH/audit" -H 'content-type: application/json' \
  -d "{\"agent_id\":\"notifier\",\"event_type\":\"agent_stop\",\"proposal_id\":\"$PID\",\"payload\":{\"reason\":\"poll_timeout\"}}" >/dev/null
exit 1
