"""Drift-catcher for infra/proposal_policy.toml.

Asserts the action_types land on the correct list and that the server
still 400s + audit_flags a self-approve attempt on each never_auto_approve
entry. The pattern mirrors
tests/test_proposals.py::test_self_approve_attempt_rejected.
"""

from __future__ import annotations

import tomllib
import uuid
from pathlib import Path

import pytest
from sqlmodel import select

from infra.db import AuditLog, Proposal

POLICY_PATH = Path(__file__).resolve().parent.parent / "infra" / "proposal_policy.toml"

# External-facing / irreversible / money-spending actions: these must ALWAYS
# require a human decision. Keep in sync with infra/proposal_policy.toml.
NEVER_AUTO_APPROVE = [
    "publish_content",
    "external_api_write",
    "send_email",
    "charge_card",
    "deploy_service",
    "purchase",
    "modify_credentials",
    "spawn_subagent",
]


def _policy() -> dict:
    return tomllib.loads(POLICY_PATH.read_text(encoding="utf-8"))


def _never() -> set[str]:
    return set(_policy().get("never_auto_approve", {}).get("action_types", []))


def _auto() -> set[str]:
    return set(_policy().get("auto_approve", {}).get("action_types", []))


def test_external_actions_are_never_auto_approve() -> None:
    """Every external-facing / irreversible / money-spending action must
    require a human decision."""
    never = _never()
    for action_type in NEVER_AUTO_APPROVE:
        assert action_type in never, f"{action_type!r} must be on never_auto_approve"


def test_reversible_internal_actions_are_auto_approve_eligible() -> None:
    auto = _auto()
    for action_type in ("research_only", "internal_file_op", "draft_content"):
        assert action_type in auto, f"{action_type!r} must be on auto_approve"


def test_action_types_not_double_listed() -> None:
    """Defensive: no action type appears on both lists."""
    overlap = _auto() & _never()
    assert overlap == set(), f"action types on both lists: {sorted(overlap)}"


def test_default_requires_approval_is_true() -> None:
    """An action_type on neither list must default to requiring approval."""
    defaults = _policy().get("defaults", {})
    assert defaults.get("requires_approval") is True


@pytest.mark.parametrize("action_type", NEVER_AUTO_APPROVE)
def test_self_approve_attempt_rejected_for_each_never_action(
    action_type: str, client, proposal_body, session
) -> None:
    """Exercise the audit_flag path for every never_auto_approve action.
    If any regresses to auto-approve, this catches it loudly."""
    pid = str(uuid.uuid4())
    body = proposal_body(
        proposal_id=pid,
        action_type=action_type,
        requires_approval=False,
    )
    response = client.post("/proposals", json=body)
    assert response.status_code == 400, response.text
    detail = response.json()["detail"]
    assert detail["error"] == "self_approve_blocked"
    assert detail["audit_flagged"] is True

    # Proposal NOT persisted.
    assert session.get(Proposal, pid) is None

    # Audit-flag event recorded against this proposal_id.
    rows = session.exec(
        select(AuditLog).where(AuditLog.event_type == "audit_flag_self_approve_attempt")
    ).all()
    assert any(r.proposal_id == pid for r in rows), (
        f"no audit_flag_self_approve_attempt for {action_type!r}"
    )


def test_draft_content_auto_approves_when_reversible(client, proposal_body, session) -> None:
    """draft_content + reversible=true + requires_approval=false must
    short-circuit to status='auto-approved'."""
    pid = str(uuid.uuid4())
    body = proposal_body(
        proposal_id=pid,
        action_type="draft_content",
        requires_approval=False,
        reversible=True,
    )
    response = client.post("/proposals", json=body)
    assert response.status_code == 200, response.text
    stored = session.get(Proposal, pid)
    assert stored is not None
    # auto-approve path sets status="auto-approved", decided_by="auto_policy".
    # If this string drifts, the dashboard filter and the watchdog
    # stuck-detector both break.
    assert stored.status == "auto-approved", (
        f"draft_content should auto-approve when reversible=true; got status={stored.status!r}"
    )
    assert stored.decided_by == "auto_policy"
