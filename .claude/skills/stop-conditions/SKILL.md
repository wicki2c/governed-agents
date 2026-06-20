---
name: stop-conditions
description: When a runtime agent must stop. Hard stops, soft stops, loop self-detection, and the exit-cleanly contract.
---

# Skill: stop-conditions (v2)

## Purpose
Define when a runtime agent must stop, regardless of task progress. The
cheap layer is the agent itself (this skill). The robust layer is the
watchdog (`infra/watchdog.py`), which can pause any agent without that
agent's cooperation.

## Hard stops — exit immediately, no completion attempt

1. **Budget paused** — `GET /budget/{agent_id}` returns `status=paused`.
2. **Watchdog pause signal** — `GET /status/me?agent_id={agent_id}`
   returns `status=paused`.
3. **Orchestrator unreachable** — three consecutive calls to
   `/status/me` fail (connection refused, timeout, non-2xx). The
   framework is down; do not proceed without supervision.
4. **Proposal rejected with `decision_final=true`** — the human has
   explicitly said no on this variant. Do not retry a near-duplicate.
5. **Terminal `result` event has `is_error=true`** — the underlying
   `claude -p` session reports a hard error (e.g.
   `subtype="error_max_budget_usd"`). The runner records this; the
   agent should not retry from within the same invocation.

## Soft stops — complete current step, then exit

1. **Time cap** — 30 minutes wall-clock from agent start. The runner
   enforces this with SIGTERM (then SIGKILL 5s later) at
   `PER_TASK_WALLCLOCK_SECONDS` (default `1800`).
2. **Step cap** — 10 tool calls within one invocation.
3. **Repeated failure** — same error from the same tool 3 times in a row.
4. **Budget warning** — `GET /budget` returns `remaining_pct < 25`.

## Loop self-detection (agent-side; watchdog backstops)

The agent should self-monitor. If the agent finds itself:
- Submitting proposals whose `payload_hash` matches any of the last 3 →
  **soft stop**. (The watchdog will also catch this as `loop_pattern`
  at window 3; getting in front of it is cheaper.)
- Calling the same tool with the same args 3+ times → **hard stop**.
- Approaching the time cap with no proposal submitted → **hard stop**.

`payload_hash` is computed server-side as `sha256(json.dumps(payload,
sort_keys=True))`, so deterministic payload differences (e.g. ordering)
do not bypass detection.

## Watchdog detection (cross-reference to `infra/watchdog.py`)

The watchdog runs as a separate process (`python -m infra.watchdog`),
polls every 60s, and fires these rules:

| Rule | Function | Trigger |
|---|---|---|
| `stuck_task` | `detect_stuck_tasks` | Proposal `status=approved`, `decided_at` older than `STUCK_TASK_MAX_AGE` (default 30 min), no `agent_stop` or `action_executed` audit row for that proposal_id. |
| `budget_cap_exceeded` | `detect_budget_breaches` | `sum(BudgetLedger.tokens) > tokens_cap` OR `sum(BudgetLedger.dollars) > dollars_cap`. |
| `loop_pattern` | `detect_loop_patterns` | The agent's last `LOOP_DETECTION_WINDOW` (default 3) proposals all share one `payload_hash`. |

When any rule fires, the watchdog flips `agents.status` to `paused` and
writes a `watchdog_pause` audit event with the reason. The agent's next
`GET /status/me` will return `paused` — at which point the agent must
hard stop.

## On stop — the exit-cleanly contract

Before exiting, an agent MUST:

1. **Write a brief note** to `agents/<lane>/memory.md` explaining why it
   stopped and what the next invocation should know. One paragraph is
   enough. This is the only persistent agent state across invocations.
2. **Submit a final audit entry** via `POST /audit`:

   ```bash
   curl -sX POST http://127.0.0.1:8005/audit \
     -H 'content-type: application/json' \
     -d '{
       "agent_id": "<lane>",
       "event_type": "agent_stop",
       "task_id": "<current task uuid or null>",
       "payload": {"reason": "hard_stop_budget_paused | soft_stop_time_cap | ..."}
     }'
   ```

3. **Exit with code 0** if the stop was graceful (any of the conditions
   above), or non-zero only if the underlying tool errored. The
   orchestrator runner reads the terminal `result` event for the real
   verdict, not the shell exit code.

## What agents must NOT do on stop

- Do not retry the same payload. The watchdog will see the pattern.
- Do not write to any file outside `agents/<lane>/`.
- Do not delete `memory.md` from a prior run. Append, don't replace,
  unless the wave instructs otherwise.
