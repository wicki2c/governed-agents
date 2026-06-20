"""POST /budget/{agent_id}/charge + GET /budget/{agent_id} + auto-pause."""

from __future__ import annotations

from infra.db import Agent


def test_charge_increments_ledger_and_returns_shape(client, session):
    response = client.post(
        "/budget/noop/charge",
        json={"tokens": 1500, "tool": "claude_call", "task_id": "t1", "dollars": 0.02},
    )
    assert response.status_code == 200, response.text
    data = response.json()
    assert data["tokens_consumed"] == 1500
    assert abs(data["dollars_consumed"] - 0.02) < 1e-9
    assert data["status"] == "ok"
    assert "remaining_pct" in data

    # Second charge accumulates.
    response2 = client.post(
        "/budget/noop/charge",
        json={"tokens": 500, "tool": "claude_call", "dollars": 0.01},
    )
    assert response2.json()["tokens_consumed"] == 2000


def test_charge_over_cap_pauses_agent(client, session):
    """The inline check in POST /budget/{id}/charge must flip
    Agent.status='paused' when dollars exceed cap. Default cap is $50.
    """
    response = client.post(
        "/budget/noop/charge",
        json={"tokens": 1, "tool": "other", "dollars": 999.99},
    )
    assert response.status_code == 200, response.text
    data = response.json()
    assert data["status"] == "paused"

    session.expire_all()
    agent = session.get(Agent, "noop")
    assert agent is not None
    assert agent.status == "paused"
    assert agent.paused_reason == "budget_cap_exceeded"


def test_get_budget_shape_matches_skill(client):
    """GET /budget/{agent_id} returns exactly the keys the budget-check
    SKILL.md contract specifies, in the right types.
    """
    response = client.get("/budget/noop")
    assert response.status_code == 200, response.text
    data = response.json()
    required_keys = {
        "agent_id",
        "wave",
        "tokens_consumed",
        "tokens_cap",
        "dollars_consumed",
        "dollars_cap",
        "status",
        "remaining_pct",
    }
    assert required_keys.issubset(set(data.keys())), data
    assert data["agent_id"] == "noop"
    assert isinstance(data["tokens_consumed"], int)
    assert isinstance(data["dollars_consumed"], (int, float))
    assert data["status"] in {"ok", "warning", "paused"}
    assert 0.0 <= data["remaining_pct"] <= 100.0
