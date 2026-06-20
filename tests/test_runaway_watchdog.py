"""Watchdog runaway scenarios for the noop agent.

These tests bypass `claude -p` entirely. They seed SQLite rows that
simulate runaway patterns, then call `infra.watchdog.run_one_pass`. Each
scenario asserts: (a) the agent is paused, (b) `paused_reason` matches
the rule that fired, (c) a `watchdog_pause` audit event was written.

The detection logic is the load-bearing part and must not actually spend
money, so it is exercised directly against seeded rows here.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlmodel import Session, select

from infra import watchdog
from infra.db import Agent, AuditLog, BudgetLedger, Proposal, record_audit


def _seed_proposal(
    session: Session,
    *,
    agent_id: str,
    payload: dict,
    submitted_at: datetime,
    status: str = "pending",
    decided_at: datetime | None = None,
) -> Proposal:
    payload_json = json.dumps(payload, sort_keys=True)
    payload_hash = hashlib.sha256(payload_json.encode("utf-8")).hexdigest()
    p = Proposal(
        proposal_id=str(uuid.uuid4()),
        agent_id=agent_id,
        wave=2,
        submitted_at=submitted_at,
        action_type="publish_content",
        payload=payload_json,
        payload_hash=payload_hash,
        expected_outcome="seeded by test",
        expires_at=submitted_at + timedelta(hours=1),
        status=status,
        decided_at=decided_at,
        decision="approve" if status == "approved" else None,
        decided_by="human" if status == "approved" else None,
    )
    session.add(p)
    session.flush()
    return p


def _last_watchdog_pause(session: Session, agent_id: str) -> AuditLog | None:
    rows = session.exec(
        select(AuditLog)
        .where(AuditLog.event_type == "watchdog_pause")
        .where(AuditLog.agent_id == agent_id)
        .order_by(AuditLog.audit_id.desc())  # type: ignore[attr-defined]
    ).all()
    return rows[0] if rows else None


def test_loop_pattern_paused_noop_with_three_identical_payloads(session: Session) -> None:
    """Three proposals with identical payload_hash → loop_pattern pause."""
    base = datetime.now(UTC) - timedelta(minutes=5)
    payload = {"topic": "runaway-loop", "iteration": "always-same"}
    for i in range(3):
        _seed_proposal(
            session,
            agent_id="noop",
            payload=payload,
            submitted_at=base + timedelta(seconds=i),
        )
    session.commit()

    events = watchdog.run_one_pass(session, now=datetime.now(UTC))
    reasons = {(e.agent_id, e.reason) for e in events}
    assert ("noop", "loop_pattern") in reasons

    agent = session.get(Agent, "noop")
    assert agent is not None
    assert agent.status == "paused"
    assert agent.paused_reason == "loop_pattern"

    audit = _last_watchdog_pause(session, "noop")
    assert audit is not None
    audit_payload = json.loads(audit.payload)
    assert audit_payload["reason"] == "loop_pattern"
    assert audit_payload["window"] == watchdog.LOOP_DETECTION_WINDOW


def test_stuck_task_paused_noop_after_approval_but_no_execution(session: Session) -> None:
    """An approved proposal aged > STUCK_TASK_MAX_AGE with no
    action_executed audit → stuck_task pause."""
    now = datetime.now(UTC)
    decided = now - watchdog.STUCK_TASK_MAX_AGE - timedelta(minutes=5)
    _seed_proposal(
        session,
        agent_id="noop",
        payload={"topic": "stuck"},
        submitted_at=decided - timedelta(minutes=1),
        status="approved",
        decided_at=decided,
    )
    session.commit()

    events = watchdog.run_one_pass(session, now=now)
    reasons = {(e.agent_id, e.reason) for e in events}
    assert ("noop", "stuck_task") in reasons

    agent = session.get(Agent, "noop")
    assert agent is not None
    assert agent.status == "paused"
    assert agent.paused_reason == "stuck_task"

    audit = _last_watchdog_pause(session, "noop")
    assert audit is not None
    audit_payload = json.loads(audit.payload)
    assert audit_payload["reason"] == "stuck_task"


@pytest.mark.parametrize(
    "terminal_event_type",
    [
        "action_executed",
        "agent_stop",
        # The generic error event the orchestrator writes when a decision
        # hook raises is also terminal for the agent's role. This list
        # matches `TERMINAL_AUDIT_EVENTS` in infra/watchdog.py.
        "audit_flag_decision_hook_error",
    ],
)
def test_terminal_events_are_not_stuck(session: Session, terminal_event_type: str) -> None:
    """An approved proposal whose terminal audit row carries its
    proposal_id must NOT be flagged stuck, for any event listed in
    TERMINAL_AUDIT_EVENTS.

    Mirrors `test_stuck_task_paused_noop_after_approval_but_no_execution`
    but adds the terminal row before running the pass."""
    now = datetime.now(UTC)
    decided = now - watchdog.STUCK_TASK_MAX_AGE - timedelta(minutes=5)
    p = _seed_proposal(
        session,
        agent_id="noop",
        payload={"topic": "hook-terminal", "event": terminal_event_type},
        submitted_at=decided - timedelta(minutes=1),
        status="approved",
        decided_at=decided,
    )
    # The hook (or app.py's wrapper) writes a row with proposal_id set.
    record_audit(
        session,
        event_type=terminal_event_type,
        agent_id="noop",
        proposal_id=p.proposal_id,
        payload={"slug": "hook-terminal-smoke"},
    )
    session.commit()

    events = watchdog.run_one_pass(session, now=now)
    stuck_for_noop = [e for e in events if e.agent_id == "noop" and e.reason == "stuck_task"]
    assert stuck_for_noop == [], (
        f"watchdog flagged stuck despite {terminal_event_type} carrying proposal_id"
    )

    agent = session.get(Agent, "noop")
    assert agent is not None
    assert agent.status != "paused", (
        f"agent wrongly paused after terminal event {terminal_event_type}"
    )
    assert agent.paused_reason is None


def test_budget_cap_exceeded_paused_noop(session: Session) -> None:
    """BudgetLedger sum over dollars_cap → budget_cap_exceeded pause."""
    agent = session.get(Agent, "noop")
    assert agent is not None
    # Tiny cap so we can trip it with one charge row
    agent.dollars_cap = 0.01
    session.add(agent)
    session.add(
        BudgetLedger(
            agent_id="noop",
            tokens=1000,
            dollars=0.05,  # > 0.01 cap
            tool="claude_call",
        )
    )
    session.commit()

    events = watchdog.run_one_pass(session, now=datetime.now(UTC))
    reasons = {(e.agent_id, e.reason) for e in events}
    assert ("noop", "budget_cap_exceeded") in reasons

    agent_after = session.get(Agent, "noop")
    assert agent_after is not None
    assert agent_after.status == "paused"
    assert agent_after.paused_reason == "budget_cap_exceeded"


def test_watchdog_pause_is_idempotent_on_second_pass(session: Session) -> None:
    """Second pass on the same state must not write a duplicate
    watchdog_pause audit row."""
    base = datetime.now(UTC) - timedelta(minutes=5)
    payload = {"topic": "idempotent"}
    for i in range(3):
        _seed_proposal(
            session,
            agent_id="noop",
            payload=payload,
            submitted_at=base + timedelta(seconds=i),
        )
    session.commit()

    watchdog.run_one_pass(session, now=datetime.now(UTC))
    audits_first = session.exec(
        select(AuditLog).where(AuditLog.event_type == "watchdog_pause")
    ).all()

    watchdog.run_one_pass(session, now=datetime.now(UTC))
    audits_second = session.exec(
        select(AuditLog).where(AuditLog.event_type == "watchdog_pause")
    ).all()

    assert len(audits_second) == len(audits_first)  # no duplicate


@pytest.mark.parametrize(
    "rule,expected_reason",
    [
        ("loop_pattern", "loop_pattern"),
        ("stuck_task", "stuck_task"),
        ("budget_cap_exceeded", "budget_cap_exceeded"),
    ],
)
def test_each_rule_writes_distinct_paused_reason(
    session: Session, rule: str, expected_reason: str
) -> None:
    """Drift catcher: paused_reason strings the watchdog writes must
    match the literals the runner's stop-conditions skill references."""
    if rule == "loop_pattern":
        base = datetime.now(UTC) - timedelta(minutes=5)
        payload = {"topic": rule}
        for i in range(3):
            _seed_proposal(
                session,
                agent_id="noop",
                payload=payload,
                submitted_at=base + timedelta(seconds=i),
            )
    elif rule == "stuck_task":
        now = datetime.now(UTC)
        decided = now - watchdog.STUCK_TASK_MAX_AGE - timedelta(minutes=5)
        _seed_proposal(
            session,
            agent_id="noop",
            payload={"topic": rule},
            submitted_at=decided - timedelta(minutes=1),
            status="approved",
            decided_at=decided,
        )
    elif rule == "budget_cap_exceeded":
        agent = session.get(Agent, "noop")
        assert agent is not None
        agent.dollars_cap = 0.01
        session.add(agent)
        session.add(BudgetLedger(agent_id="noop", tokens=1000, dollars=0.05, tool="claude_call"))
    session.commit()

    watchdog.run_one_pass(session, now=datetime.now(UTC))
    agent = session.get(Agent, "noop")
    assert agent is not None
    assert agent.paused_reason == expected_reason
