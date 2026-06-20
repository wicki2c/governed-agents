"""Watchdog detection rules: stuck task, loop pattern, budget breach."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlmodel import select

from infra.db import Agent, AuditLog, BudgetLedger, Proposal
from infra.watchdog import (
    detect_loop_patterns,
    detect_stuck_tasks,
    run_one_pass,
)


def _approved_proposal(
    *,
    proposal_id: str,
    agent_id: str = "noop",
    decided_minutes_ago: int = 31,
    payload_hash: str = "abc",
    now: datetime | None = None,
) -> Proposal:
    now = now or datetime.now(UTC)
    return Proposal(
        proposal_id=proposal_id,
        agent_id=agent_id,
        wave=1,
        submitted_at=now - timedelta(minutes=decided_minutes_ago + 1),
        action_type="publish_content",
        payload="{}",
        payload_hash=payload_hash,
        expected_outcome="t",
        expires_at=now + timedelta(hours=1),
        status="approved",
        decided_by="human",
        decided_at=now - timedelta(minutes=decided_minutes_ago),
        decision="approve",
    )


def test_stuck_task_paused(session, now):
    """Acceptance bullet: a stuck task (approved >30min ago, no terminal
    audit event) is paused on the next watchdog pass.
    """
    session.add(_approved_proposal(proposal_id="p1", decided_minutes_ago=35, now=now))
    session.commit()

    events = run_one_pass(session, now=now)
    reasons = {(e.agent_id, e.reason) for e in events}
    assert ("noop", "stuck_task") in reasons

    session.expire_all()
    agent = session.get(Agent, "noop")
    assert agent.status == "paused"
    assert agent.paused_reason == "stuck_task"

    pause_events = session.exec(
        select(AuditLog)
        .where(AuditLog.event_type == "watchdog_pause")
        .where(AuditLog.agent_id == "noop")
    ).all()
    assert len(pause_events) == 1


def test_stuck_task_not_paused_when_terminal_event_present(session, now):
    """A proposal older than the threshold is NOT stuck if the agent
    has already emitted an agent_stop or action_executed audit event.
    """
    session.add(_approved_proposal(proposal_id="p2", decided_minutes_ago=40, now=now))
    session.add(
        AuditLog(
            ts=now - timedelta(minutes=30),
            agent_id="noop",
            event_type="agent_stop",
            proposal_id="p2",
        )
    )
    session.commit()

    events = detect_stuck_tasks(session, now=now)
    assert not any(e.agent_id == "noop" for e in events)


def test_loop_pattern_paused(session, now):
    """Three proposals with the same payload_hash → loop_pattern pause."""
    for i in range(3):
        session.add(
            Proposal(
                proposal_id=f"loop-{i}",
                agent_id="noop",
                wave=1,
                submitted_at=now - timedelta(minutes=3 - i),
                action_type="draft_content",
                payload="{}",
                payload_hash="same_hash",
                expected_outcome="t",
                expires_at=now + timedelta(hours=1),
                status="pending",
            )
        )
    session.commit()

    events = detect_loop_patterns(session, now=now)
    assert any(e.agent_id == "noop" and e.reason == "loop_pattern" for e in events)


def test_budget_breach_paused_by_watchdog(session, now):
    """Watchdog is defense-in-depth past the inline check. Insert ledger
    rows that put dollars_consumed above cap, then run a pass."""
    agent = session.get(Agent, "noop")
    agent.dollars_cap = 1.00
    session.add(agent)
    # 2 rows totaling $1.50 (over $1.00 cap)
    session.add(BudgetLedger(agent_id="noop", tokens=1, dollars=0.80, tool="other"))
    session.add(BudgetLedger(agent_id="noop", tokens=1, dollars=0.70, tool="other"))
    session.commit()

    events = run_one_pass(session, now=now)
    reasons = {(e.agent_id, e.reason) for e in events}
    assert ("noop", "budget_cap_exceeded") in reasons

    session.expire_all()
    refreshed = session.get(Agent, "noop")
    assert refreshed.status == "paused"


def test_watchdog_pause_is_idempotent(session, now):
    session.add(_approved_proposal(proposal_id="p3", decided_minutes_ago=60, now=now))
    session.commit()
    first = run_one_pass(session, now=now)
    run_one_pass(session, now=now)  # second pass — pause_agent must be a no-op
    assert any(e.agent_id == "noop" for e in first)
    # Second pass detects again but pause_agent is no-op (already paused)
    pause_events = session.exec(
        select(AuditLog)
        .where(AuditLog.event_type == "watchdog_pause")
        .where(AuditLog.agent_id == "noop")
    ).all()
    assert len(pause_events) == 1, (
        f"expected one pause event after idempotent re-run, got {len(pause_events)}"
    )
