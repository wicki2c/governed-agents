"""Per-agent status endpoint and admin pause-all panic button.

Coverage added in response to pre-PR code-reviewer FLAG #2 — the
agent-stop-condition signal (`GET /status/me`) and the privileged
write path (`POST /admin/pause-all`) had no test coverage in step (g).
"""

from __future__ import annotations

from sqlmodel import select

from infra.db import Agent, AuditLog


def test_status_me_returns_skill_contract_shape(client, session):
    response = client.get("/status/me?agent_id=noop")
    assert response.status_code == 200, response.text
    body = response.json()
    assert set(body.keys()) >= {"agent_id", "status", "paused_reason"}
    assert body["agent_id"] == "noop"
    assert body["status"] in {"ok", "warning", "paused"}
    assert body["paused_reason"] is None


def test_status_me_unknown_agent_returns_404(client):
    response = client.get("/status/me?agent_id=does_not_exist")
    assert response.status_code == 404


def test_status_me_reflects_paused_agent(client, session):
    agent = session.get(Agent, "noop")
    agent.status = "paused"
    agent.paused_reason = "test_reason"
    session.add(agent)
    session.commit()

    response = client.get("/status/me?agent_id=noop")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "paused"
    assert body["paused_reason"] == "test_reason"


def test_admin_pause_all_pauses_every_known_agent(client, session):
    response = client.post("/admin/pause-all", json={"reason": "smoke_test_panic"})
    assert response.status_code == 200, response.text
    body = response.json()
    assert "paused_agents" in body
    # Both seeded agents (noop, orchestrator) were status='ok' going in;
    # both should now be reported as paused.
    assert len(body["paused_agents"]) == 2

    session.expire_all()
    agents = session.exec(select(Agent)).all()
    assert all(a.status == "paused" for a in agents)
    assert all(a.paused_reason == "smoke_test_panic" for a in agents)

    # Audit log records the panic.
    rows = session.exec(select(AuditLog).where(AuditLog.event_type == "admin_pause_all")).all()
    assert len(rows) == 1


def test_admin_pause_all_is_idempotent(client, session):
    client.post("/admin/pause-all", json={"reason": "first"})
    response = client.post("/admin/pause-all", json={"reason": "second"})
    assert response.status_code == 200
    # All agents were already paused after the first call, so the second
    # reports an empty paused_agents list (no state change).
    assert response.json()["paused_agents"] == []


def test_status_endpoint_lists_paused_agents(client, session):
    client.post("/admin/pause-all", json={"reason": "test"})

    response = client.get("/status")
    assert response.status_code == 200
    body = response.json()
    assert set(body["agents_paused"]) == {
        "noop",
        "orchestrator",
    }
