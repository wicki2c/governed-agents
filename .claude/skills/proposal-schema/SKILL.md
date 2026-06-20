---
name: proposal-schema
description: Contract for submitting proposals to the orchestration server. Every runtime agent reads this before constructing a POST /proposals body. Authoritative over training memory.
---

# Skill: proposal-schema

## Purpose
Define the wire-level contract for `POST /proposals` on the orchestration
server (`http://127.0.0.1:8005`). Runtime agents MUST construct request
bodies exactly as specified here. The server (`infra/app.py:ProposalSubmit`)
and the policy file (`infra/proposal_policy.toml`) are the ground truth; if
this file ever drifts from them, fix this file.

## Endpoint
- Submit: `POST http://127.0.0.1:8005/proposals`
- Read decision: `GET http://127.0.0.1:8005/proposals/{proposal_id}`
- Decide (human / dashboard only — agents must not call this):
  `POST http://127.0.0.1:8005/proposals/{proposal_id}/decision`

## Request body — required fields

| Field | Type | Notes |
|---|---|---|
| `agent_id` | `str` | Your agent's id (see `infra/db.py:KNOWN_AGENT_IDS`; the bundled demo uses `noop`). |
| `wave` | `int` | An integer you can use to group runs into batches. Use `0` if you don't need it. |
| `submitted_at` | ISO‑8601 UTC | `datetime.now(UTC).isoformat()`. |
| `action_type` | `str` | From the taxonomy below. |
| `payload` | `object` | Action-specific. Sorted JSON serialisation contributes to `payload_hash`, which the watchdog uses for loop detection. |
| `expected_outcome` | `str` | One sentence; surfaces on the dashboard. |
| `expires_at` | ISO‑8601 UTC | The server does not auto-reject expired proposals today, but the agent MUST stop polling past this time. |

## Request body — optional fields (defaults shown)

| Field | Type | Default | Notes |
|---|---|---|---|
| `proposal_id` | `str` (UUID v4) | `null` — **server generates** | **Preferred:** omit and let the server generate a UUIDv4; the value is echoed in the response. Client-provided IDs are still accepted for back-compat. Uniqueness is enforced by the DB primary key either way; collisions return 409. |
| `expected_cost_tokens` | `int` | `0` | Best estimate. The actual charge comes from the runner. |
| `expected_cost_dollars` | `float` | `0.0` | Same. |
| `reversible` | `bool` | `true` | **If `false`, the server coerces `requires_approval` to `true` regardless of policy.** |
| `requires_approval` | `bool` | `true` | See behaviour table below. |
| `rationale` | `str` | `""` | 2–4 sentences justifying the action. |
| `links` | `list[str]` | `[]` | URLs to diff / preview. |

## Action-type taxonomy (mirrors `infra/proposal_policy.toml`)

The set below is the framework's generic starter policy. Replace it with
the actions your own agents take; the rule of thumb is on the right.

**Auto-approve eligible** (set `requires_approval=false` + `reversible=true` to actually auto-approve) — reversible, side-effect-free:
- `research_only` — fetch / read external sources, no writes
- `internal_file_op` — write inside the agent's own `workspace/`
- `draft_content` — produce a draft, not publish it

**Never auto-approve** (server rejects with HTTP 400 + audit-flag if you set `requires_approval=false`) — external-facing, irreversible, or money-spending:
- `publish_content` — make something publicly visible
- `external_api_write` — any state-changing call to a third-party API
- `send_email` — outbound message to a person
- `charge_card` — spend real money
- `deploy_service` — provision or update a live deployment
- `purchase` — buy a domain / good / subscription
- `modify_credentials` — rotate or change a secret
- `spawn_subagent` — widen the agent topology

**Default-gated** — any `action_type` not on either list above defaults to
`requires_approval=true` per `[defaults]` in `proposal_policy.toml`. `"other"`
falls here. This works, but agents should prefer an enumerated type; use
`"other"` only when nothing fits.

## Server behaviour table (from `infra/app.py:submit_proposal`)

| `action_type` on… | `requires_approval` | `reversible` | Resulting `status` |
|---|---|---|---|
| `never_auto_approve` | `false` | any | **HTTP 400** + `audit_flag_self_approve_attempt` event |
| `never_auto_approve` | `true` | any | `pending` (awaits human) |
| `auto_approve` | `false` | `true` | `auto-approved` (decided_by = `auto_policy`) |
| `auto_approve` | `false` | `false` | `pending` (irreversibility wins) |
| `auto_approve` | `true` | any | `pending` |
| anything else | any | any | `pending` |

## Response — `POST /proposals`

```json
{
  "proposal_id": "<echoed-or-server-generated>",
  "status": "pending | auto-approved",
  "review_url": "http://127.0.0.1:8005/#proposal-<id>"
}
```

If you omitted `proposal_id` in the request, the field in the response is
the server-generated UUIDv4. **Use that value for all subsequent polls
and audit posts** (`GET /proposals/{id}`, `POST /audit` with
`proposal_id`). The agent does not need to know whether the id came from
itself or the server — read it from the response either way.

Non‑2xx responses:
- `400` — self-approve attempt on a `never_auto_approve` action_type. **Do not retry.**
- `409` — `proposal_id` already exists (only reachable when the client provided one).

## Polling for a decision

```bash
curl -s "http://127.0.0.1:8005/proposals/$PID"
```

Body contains `status` ∈ `{pending, approved, rejected, auto-approved}`,
`decided_by`, `decided_at`, `decision_final`. The agent gates execution
on `status == "approved"` OR `status == "auto-approved"`. If
`status == "rejected"` AND `decision_final == true`, the agent must
NOT retry a variant (per `stop-conditions/SKILL.md`).

Poll cadence: every 5–10 seconds. Stop polling at `expires_at`.

## Worked example — a smoke proposal

```bash
NOW=$(python3 -c 'from datetime import datetime, timezone; print(datetime.now(timezone.utc).isoformat())')
EXP=$(python3 -c 'from datetime import datetime, timezone, timedelta; print((datetime.now(timezone.utc) + timedelta(minutes=15)).isoformat())')

RESPONSE=$(curl -sX POST http://127.0.0.1:8005/proposals \
  -H 'content-type: application/json' \
  -d "{
    \"agent_id\":\"noop\",
    \"wave\":0,
    \"submitted_at\":\"$NOW\",
    \"action_type\":\"research_only\",
    \"payload\":{\"message\":\"smoke\"},
    \"expected_outcome\":\"Smoke proposal approved end-to-end.\",
    \"reversible\":true,
    \"requires_approval\":true,
    \"expires_at\":\"$EXP\",
    \"rationale\":\"Framework smoke test.\"
  }")
PID=$(echo "$RESPONSE" | python3 -c 'import json,sys;print(json.load(sys.stdin)["proposal_id"])')
```

Note: `proposal_id` is omitted from the request body. The server generates
it and returns it in `RESPONSE`. Use `$PID` for all subsequent polls and
audit posts.

## Hard rules for agents

1. **Never** set `requires_approval=false` on a `never_auto_approve`
   `action_type`. The attempt is logged as a self-approve violation
   (`audit_flag_self_approve_attempt`).
2. **Never** call `POST /proposals/{id}/decision`. That endpoint is for
   the dashboard / human.
3. **Prefer** to omit `proposal_id` and read the server-generated value
   from the response. If you do provide one, ensure it's fresh; reuse
   returns 409.
4. **Always** include a non-empty `rationale` justifying the action.
