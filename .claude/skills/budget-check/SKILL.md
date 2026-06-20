---
name: budget-check
description: Budget ledger contract. Runtime agents check status before/after work; the orchestrator runner posts the charges. Agents do not self-charge.
---

# Skill: budget-check (v2)

## Purpose
Every runtime agent must reason about its budget. The budget ledger lives
in the orchestration server. Two contracts here:
1. **The agent reads** `GET /budget/{agent_id}` to decide whether to keep
   going (see `stop-conditions/SKILL.md`).
2. **The orchestrator runner — not the agent — posts charges** via
   `POST /budget/{agent_id}/charge`, with tokens extracted from the
   `claude -p` stream-json `result` event. This is honesty by
   construction: the agent cannot under-report itself out of a cap.

## Endpoints

### `GET http://127.0.0.1:8005/budget/{agent_id}`

```json
{
  "agent_id": "noop",
  "wave": 0,
  "tokens_consumed": 142500,
  "tokens_cap": 200000,
  "dollars_consumed": 1.42,
  "dollars_cap": 50.00,
  "status": "ok | warning | paused",
  "remaining_pct": 71.25
}
```

### `POST http://127.0.0.1:8005/budget/{agent_id}/charge`

```json
{
  "tokens": 12345,
  "tool": "claude_call | web_search | api_call | other",
  "task_id": "<task uuid, optional>",
  "dollars": 0.18
}
```

Body matches `infra/app.py:BudgetCharge`. Returns the post-charge
`BudgetResponse`. **If the resulting consumption exceeds either cap, the
server flips `agents.status = "paused"` with `paused_reason="budget_cap_exceeded"`
and writes a `budget_paused` audit event.**

If `dollars` is omitted, the server derives it from `tokens / 1000 *
DEFAULT_DOLLARS_PER_1K_TOKENS` (currently `0.015`, see `infra/db.py`).
The runner SHOULD pass `dollars` explicitly when the stream-json
`result.modelUsage[*].costUSD` field is present — it's more accurate.

## Decision rules for the agent (from `GET /budget`)

| `status` | `remaining_pct` | What the agent does |
|---|---|---|
| `paused` | any | **Exit immediately.** Do not retry. Do not POST. The watchdog or budget breach has determined this agent is unsafe. |
| `warning` or `ok` | `< 10` | Exit after the current tool call. |
| `warning` or `ok` | `< 25` | Finish the current task, then exit. Do not start a new task. |
| `ok` | `≥ 25` | Continue normally. |

Check the budget at the start of every invocation and after each
significant tool call.

## Charging contract (the runner does this; agents read only)

The orchestrator runner (`agents/orchestrator/runner.py`) extracts tokens
from the terminal `result` event of `claude -p --output-format stream-json
--verbose`:

```json
{
  "type": "result",
  "subtype": "success | error_max_budget_usd | ...",
  "is_error": false,
  "duration_ms": 8421,
  "num_turns": 3,
  "total_cost_usd": 0.018,
  "modelUsage": {
    "claude-opus-4-7": {
      "inputTokens": 1240,
      "outputTokens": 412,
      "cacheReadInputTokens": 0,
      "cacheCreationInputTokens": 0,
      "costUSD": 0.018
    }
  },
  "permission_denials": [],
  "terminal_reason": "...",
  "errors": []
}
```

Tokens charged = `sum(input + output + cache_creation + cache_read)` across
all entries in `modelUsage`. Dollars charged = `total_cost_usd` (or sum of
`costUSD` per model) when present.

**If the `result` event is missing or `modelUsage` is empty:** the runner
charges `tokens=0, dollars=0` AND writes a `budget_charge_unknown_usage`
audit event. This is visible by design — it should NOT happen silently.

## What agents must NOT do

1. **Never** POST to `/budget/{agent_id}/charge`. Only the orchestrator
   runner is allowed. Agent allowlists do not include this curl pattern.
2. **Never** estimate tokens and round down. The runner reads real
   numbers from stream-json.
3. **Never** continue work after `GET /budget` returns `status=paused`.
   Exit; write `agents/<lane>/memory.md`; let the human investigate.

## Honesty by construction
Under-reporting token consumption to evade the cap is not possible from
the agent side. Because the runner is the only writer, an agent that
tries to game the cap must instead prevent the runner from observing its
turns — which means crashing mid-stream, which surfaces as an
`agent_run_failed` audit event. The honesty layer is structural, not
behavioural.
