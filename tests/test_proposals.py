"""POST /proposals + GET /proposals/{id} + decision flow."""

from __future__ import annotations

import uuid

from sqlmodel import select

from infra.db import AuditLog, Proposal


def _new_id() -> str:
    return str(uuid.uuid4())


def test_post_proposal_persists_and_returns_id(client, proposal_body, session):
    pid = _new_id()
    body = proposal_body(proposal_id=pid)
    response = client.post("/proposals", json=body)
    assert response.status_code == 200, response.text
    data = response.json()
    assert data["proposal_id"] == pid
    assert data["status"] == "pending"
    assert data["review_url"].startswith("http://127.0.0.1:")

    stored = session.get(Proposal, pid)
    assert stored is not None
    assert stored.action_type == "publish_content"
    assert stored.status == "pending"


def test_self_approve_attempt_rejected(client, proposal_body, session):
    """An agent cannot self-approve a never_auto_approve action_type. The
    server must 400 the request AND write an audit_flag event.
    """
    pid = _new_id()
    body = proposal_body(
        proposal_id=pid,
        action_type="charge_card",
        requires_approval=False,
    )
    response = client.post("/proposals", json=body)
    assert response.status_code == 400, response.text
    detail = response.json()["detail"]
    assert detail["error"] == "self_approve_blocked"
    assert detail["audit_flagged"] is True

    # Proposal must NOT have been persisted.
    assert session.get(Proposal, pid) is None

    # Audit_flag event must exist.
    rows = session.exec(
        select(AuditLog).where(AuditLog.event_type == "audit_flag_self_approve_attempt")
    ).all()
    assert any(r.proposal_id == pid for r in rows)


def test_get_proposal_returns_stored_row(client, proposal_body):
    pid = _new_id()
    client.post("/proposals", json=proposal_body(proposal_id=pid))
    response = client.get(f"/proposals/{pid}")
    assert response.status_code == 200, response.text
    data = response.json()
    assert data["proposal_id"] == pid
    assert data["status"] == "pending"
    assert data["payload"] == {"topic": "test"}


def test_duplicate_proposal_id_returns_409(client, proposal_body):
    pid = _new_id()
    body = proposal_body(proposal_id=pid)
    assert client.post("/proposals", json=body).status_code == 200
    response = client.post("/proposals", json=body)
    assert response.status_code == 409, response.text
    assert "already exists" in response.json()["detail"]


def test_decision_endpoint_records_decided_at(client, proposal_body, session):
    pid = _new_id()
    client.post("/proposals", json=proposal_body(proposal_id=pid))
    response = client.post(
        f"/proposals/{pid}/decision",
        json={"decision": "approve", "final": False, "note": "looks good"},
    )
    assert response.status_code == 200, response.text
    assert response.json()["status"] == "approved"

    stored = session.get(Proposal, pid)
    assert stored is not None
    assert stored.status == "approved"
    assert stored.decided_by == "human"
    assert stored.decided_at is not None

    # Audit row exists for the decision.
    rows = session.exec(
        select(AuditLog)
        .where(AuditLog.proposal_id == pid)
        .where(AuditLog.event_type == "proposal_decided")
    ).all()
    assert len(rows) == 1


def test_post_proposal_without_proposal_id_server_generates(client, proposal_body, session):
    """proposal_id is optional; the server generates a UUIDv4 when absent
    and returns it in the response. Persistence uses the server-generated id.
    """
    body = proposal_body(proposal_id="DROPME")
    del body["proposal_id"]

    response = client.post("/proposals", json=body)
    assert response.status_code == 200, response.text
    data = response.json()
    assert "proposal_id" in data
    assert isinstance(data["proposal_id"], str)

    # Server-generated id must be a valid UUID.
    generated = uuid.UUID(data["proposal_id"])
    assert str(generated) == data["proposal_id"]

    stored = session.get(Proposal, data["proposal_id"])
    assert stored is not None
    assert stored.action_type == "publish_content"
    assert data["review_url"].endswith(f"#proposal-{data['proposal_id']}")


def test_post_proposal_with_explicit_proposal_id_back_compat(client, proposal_body, session):
    """Back-compat path: clients that provide their own proposal_id must
    still see it echoed in the response and persisted under that key.
    """
    pid = _new_id()
    response = client.post("/proposals", json=proposal_body(proposal_id=pid))
    assert response.status_code == 200, response.text
    data = response.json()
    assert data["proposal_id"] == pid

    stored = session.get(Proposal, pid)
    assert stored is not None
    assert stored.proposal_id == pid


def test_reversible_false_forces_human_approval(client, proposal_body, session):
    """reversible=false coerces requires_approval=true, even for
    action_types on the auto_approve list.
    """
    pid = _new_id()
    body = proposal_body(
        proposal_id=pid,
        action_type="research_only",  # on auto_approve list
        requires_approval=False,
        reversible=False,
    )
    response = client.post("/proposals", json=body)
    assert response.status_code == 200, response.text
    assert response.json()["status"] == "pending"
    stored = session.get(Proposal, pid)
    assert stored is not None
    assert stored.requires_approval is True
