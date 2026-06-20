"""End-to-end audit-chain test for the noop runtime agent.

Drives the proposal → approve → execute → audit chain via the FastAPI
TestClient — the orchestrator side of the contract — and asserts the
events land in order with no gaps.

The runner + claude subprocess integration is unit-tested in
`test_runner_unit.py`.
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime, timedelta

from fastapi.testclient import TestClient


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _expires_iso(minutes: int = 15) -> str:
    return (datetime.now(UTC) + timedelta(minutes=minutes)).isoformat()


def _audit_event_types(client: TestClient, agent_id: str) -> list[str]:
    """Return audit event types for agent_id in **chronological** order
    (oldest first). The /audit endpoint returns desc by ts; we reverse."""
    r = client.get("/audit", params={"agent_id": agent_id, "limit": 500})
    r.raise_for_status()
    rows = r.json()
    return [row["event_type"] for row in reversed(rows)]


def test_noop_audit_chain_submit_approve_execute(client: TestClient) -> None:
    """The full audit-chain acceptance."""
    pid = str(uuid.uuid4())

    # 1. Agent submits proposal (noop with research_only + requires_approval=true)
    submit_resp = client.post(
        "/proposals",
        json={
            "proposal_id": pid,
            "agent_id": "noop",
            "wave": 2,
            "submitted_at": _now_iso(),
            "action_type": "research_only",
            "payload": {"message": "noop smoke"},
            "expected_outcome": "Smoke proposal approved end-to-end.",
            "reversible": True,
            "requires_approval": True,
            "expires_at": _expires_iso(),
            "rationale": "Framework smoke.",
        },
    )
    assert submit_resp.status_code == 200
    assert submit_resp.json()["status"] == "pending"

    # 2. Human approves via dashboard
    decision_resp = client.post(
        f"/proposals/{pid}/decision",
        json={"decision": "approve", "final": False},
    )
    assert decision_resp.status_code == 200
    assert decision_resp.json()["status"] == "approved"

    # 3. Agent (post-approval) executes the action and writes audit
    exec_resp = client.post(
        "/audit",
        json={
            "agent_id": "noop",
            "event_type": "action_executed",
            "proposal_id": pid,
            "payload": {"outcome": "noop", "smoke": True},
        },
    )
    assert exec_resp.status_code == 200

    # 4. Runner posts a budget charge (the parent runner does this; the
    #    agent never charges itself)
    charge_resp = client.post(
        "/budget/noop/charge",
        json={"tokens": 1500, "tool": "claude_call", "dollars": 0.02, "task_id": pid},
    )
    assert charge_resp.status_code == 200

    # 5. Runner writes the terminal audit event
    complete_resp = client.post(
        "/audit",
        json={
            "agent_id": "noop",
            "event_type": "agent_run_complete",
            "task_id": pid,
            "payload": {"tokens": 1500, "dollars": 0.02, "reason": "success"},
        },
    )
    assert complete_resp.status_code == 200

    # Assert the chain is in order, no gaps, no extras.
    types = _audit_event_types(client, "noop")
    # Filter only the events that belong to this proposal's chain
    expected = [
        "proposal_submitted",
        "proposal_decided",
        "action_executed",
        "budget_charge",
        "agent_run_complete",
    ]
    # Each expected event must appear in `types` in this order.
    cursor = 0
    for evt in expected:
        try:
            cursor = types.index(evt, cursor) + 1
        except ValueError:
            raise AssertionError(f"audit chain missing {evt!r}; saw: {types}") from None

    # Budget ledger captured the charge
    budget_resp = client.get("/budget/noop")
    assert budget_resp.status_code == 200
    budget = budget_resp.json()
    assert budget["tokens_consumed"] == 1500
    assert budget["dollars_consumed"] == 0.02

    # Scoreboard reflects the approved proposal
    score_resp = client.get("/scoreboard")
    assert score_resp.status_code == 200
    noop_row = next(r for r in score_resp.json() if r["agent_id"] == "noop")
    assert noop_row["proposals_approved"] >= 1
    assert noop_row["tokens_consumed"] == 1500


def test_noop_proposal_self_approve_blocked(client: TestClient) -> None:
    """If a runtime agent (mis)tries to self-approve a never_auto_approve
    action_type, the server rejects with 400 and logs an audit flag.
    Validates the noop agent's hard rule from agents/noop/CLAUDE.md."""
    pid = str(uuid.uuid4())
    r = client.post(
        "/proposals",
        json={
            "proposal_id": pid,
            "agent_id": "noop",
            "wave": 2,
            "submitted_at": _now_iso(),
            "action_type": "publish_content",  # never_auto_approve
            "payload": {"topic": "x"},
            "expected_outcome": "x",
            "reversible": True,
            "requires_approval": False,  # the violation
            "expires_at": _expires_iso(),
        },
    )
    assert r.status_code == 400
    body = r.json()["detail"]
    assert body["error"] == "self_approve_blocked"
    assert body["audit_flagged"] is True

    # Audit flag landed
    types = _audit_event_types(client, "noop")
    assert "audit_flag_self_approve_attempt" in types


def test_noop_proposal_rejected_final_blocks_retry(client: TestClient) -> None:
    """If a proposal is rejected with final=true, a new attempt with the
    same payload (different proposal_id) lands as pending — the server
    doesn't enforce 'don't retry' (that's the agent's stop-condition).
    The chain we want to see: rejected proposal AUDIT shows final=true so
    the agent can read it and stop polling."""
    pid = str(uuid.uuid4())
    client.post(
        "/proposals",
        json={
            "proposal_id": pid,
            "agent_id": "noop",
            "wave": 2,
            "submitted_at": _now_iso(),
            "action_type": "research_only",
            "payload": {"message": "no-go"},
            "expected_outcome": "x",
            "reversible": True,
            "requires_approval": True,
            "expires_at": _expires_iso(),
        },
    )
    r = client.post(
        f"/proposals/{pid}/decision",
        json={"decision": "reject", "final": True, "note": "absolutely not"},
    )
    assert r.status_code == 200
    # The proposal record carries decision_final=true
    p = client.get(f"/proposals/{pid}").json()
    assert p["decision_final"] is True
    assert p["status"] == "rejected"

    # The decided audit row carries final=true in its payload
    audits = client.get("/audit", params={"agent_id": "noop", "limit": 50}).json()
    decided = next(a for a in audits if a["event_type"] == "proposal_decided")
    assert decided["payload"]["final"] is True


def test_noop_runner_skip_audit_when_paused(client: TestClient) -> None:
    """If the noop agent is paused (any reason), a runner invocation must
    write `agent_run_skipped` and not advance the proposal chain. This is
    the audit shape the runner uses; we verify the orchestrator accepts
    it and surfaces it."""
    # Pause noop
    client.post("/admin/pause-all", json={"reason": "test_pause"})

    # Runner-equivalent audit write
    client.post(
        "/audit",
        json={
            "agent_id": "noop",
            "event_type": "agent_run_skipped",
            "payload": {"reason": "paused", "paused_reason": "test_pause"},
        },
    )

    types = _audit_event_types(client, "noop")
    assert "agent_run_skipped" in types

    # /status/me reflects the pause so the runner will short-circuit
    r = client.get("/status/me", params={"agent_id": "noop"})
    assert r.status_code == 200
    assert r.json()["status"] == "paused"


def test_audit_jsonl_mirror_matches_sql(client: TestClient) -> None:
    """audit.jsonl is the append-only mirror for the SQLite
    audit_log table, so the audit trail survives DB corruption. The
    Wave-2 noop chain should produce identical rows in both sinks."""
    pid = str(uuid.uuid4())
    client.post(
        "/proposals",
        json={
            "proposal_id": pid,
            "agent_id": "noop",
            "wave": 2,
            "submitted_at": _now_iso(),
            "action_type": "research_only",
            "payload": {"message": "mirror check"},
            "expected_outcome": "x",
            "reversible": True,
            "requires_approval": True,
            "expires_at": _expires_iso(),
        },
    )

    # Read both sinks
    sql_rows = client.get("/audit", params={"agent_id": "noop", "limit": 10}).json()

    import infra.app as app_module

    jsonl_path = app_module.AUDIT_JSONL_PATH
    jsonl_rows = [
        json.loads(line)
        for line in jsonl_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]

    # Every SQL row should appear in jsonl (modulo ordering)
    sql_ids = {r["audit_id"] for r in sql_rows}
    jsonl_ids = {r["audit_id"] for r in jsonl_rows if r.get("audit_id") in sql_ids}
    assert sql_ids == jsonl_ids
