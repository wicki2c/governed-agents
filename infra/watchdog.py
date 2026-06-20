"""Watchdog process. Detects stuck / over-budget / looping agents.

Runs as a standalone process (`python -m infra.watchdog`), independent
of the FastAPI orchestrator. Polls every WATCHDOG_INTERVAL_SECONDS
(default 60). Communicates with the orchestrator exclusively via the
SQLite DB — no shared in-memory state.

Detection rules (see .claude/skills/stop-conditions/SKILL.md):

  1. Stuck task — a proposal with status='approved' whose decided_at
     is older than STUCK_TASK_MAX_AGE (default 30 min) AND no subsequent
     audit_log row exists for that proposal_id with event_type in
     TERMINAL_AUDIT_EVENTS. An agent signals it has moved on by writing
     `action_executed` or `agent_stop`. If you register a decision hook
     that emits its own terminal event on success, add that event to
     TERMINAL_AUDIT_EVENTS so an approved-and-handled proposal is not
     flagged stuck.

  2. Budget breach — agent.dollars/tokens_consumed exceeds caps. The
     inline check in POST /budget/{id}/charge is the primary; the
     watchdog catches the case where the inline write failed or caps
     were lowered after the fact.

  3. Loop pattern — the agent's last LOOP_DETECTION_WINDOW proposals all
     have the same payload_hash. Default window 3.

When a rule fires, the watchdog flips Agent.status to 'paused' and writes
a watchdog_pause audit event. Pausing is idempotent.
"""

from __future__ import annotations

import os
import signal
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Literal

import structlog
from sqlmodel import Session, func, select

from infra.db import (
    Agent,
    AuditLog,
    BudgetLedger,
    Proposal,
    engine,
    init_db,
    record_audit,
    utcnow,
)

log = structlog.get_logger(__name__)

WATCHDOG_INTERVAL_SECONDS = int(os.environ.get("WATCHDOG_INTERVAL_SECONDS", "60"))
STUCK_TASK_MAX_AGE = timedelta(minutes=int(os.environ.get("WATCHDOG_STUCK_TASK_MAX_MINUTES", "30")))
LOOP_DETECTION_WINDOW = int(os.environ.get("WATCHDOG_LOOP_WINDOW", "3"))


PauseReason = Literal["stuck_task", "budget_cap_exceeded", "loop_pattern"]

# Audit event_types that mark an approved proposal as "carried out" from
# the agent's perspective, so `detect_stuck_tasks` won't flag it. The base
# set covers agent-driven completion (`action_executed` / `agent_stop`)
# and the generic decision-hook error the orchestrator writes when a hook
# raises. If you register a decision hook that emits its own terminal
# event on success, append that event name here.
TERMINAL_AUDIT_EVENTS = [
    "agent_stop",
    "action_executed",
    "audit_flag_decision_hook_error",
]


@dataclass
class PauseEvent:
    agent_id: str
    reason: PauseReason
    detail: dict


def _aware(dt: datetime | None) -> datetime | None:
    if dt is None or dt.tzinfo is not None:
        return dt
    # SQLite strips tzinfo on round-trip; we always insert UTC, so it's
    # safe to reattach UTC for comparison.
    return dt.replace(tzinfo=utcnow().tzinfo)


def detect_stuck_tasks(session: Session, now: datetime) -> list[PauseEvent]:
    cutoff = now - STUCK_TASK_MAX_AGE
    rows = session.exec(select(Proposal).where(Proposal.status == "approved")).all()
    events: list[PauseEvent] = []
    for p in rows:
        decided = _aware(p.decided_at)
        if decided is None or decided > cutoff:
            continue
        # Has any audit event since the proposal was approved indicated
        # the agent moved on?
        terminal_events = session.exec(
            select(AuditLog)
            .where(AuditLog.proposal_id == p.proposal_id)
            .where(AuditLog.event_type.in_(TERMINAL_AUDIT_EVENTS))  # type: ignore[attr-defined]
        ).first()
        if terminal_events is not None:
            continue
        events.append(
            PauseEvent(
                agent_id=p.agent_id,
                reason="stuck_task",
                detail={
                    "proposal_id": p.proposal_id,
                    "decided_at": decided.isoformat() if decided else None,
                    "age_minutes": round((now - decided).total_seconds() / 60.0, 1)
                    if decided
                    else None,
                },
            )
        )
    return events


def detect_budget_breaches(session: Session, now: datetime) -> list[PauseEvent]:
    del now  # not used; budget breach is point-in-time
    events: list[PauseEvent] = []
    agents = session.exec(select(Agent)).all()
    for agent in agents:
        totals = session.exec(
            select(
                func.coalesce(func.sum(BudgetLedger.tokens), 0),
                func.coalesce(func.sum(BudgetLedger.dollars), 0.0),
            ).where(BudgetLedger.agent_id == agent.agent_id)
        ).one()
        tokens = int(totals[0] or 0)
        dollars = float(totals[1] or 0.0)
        if tokens > agent.tokens_cap or dollars > agent.dollars_cap:
            events.append(
                PauseEvent(
                    agent_id=agent.agent_id,
                    reason="budget_cap_exceeded",
                    detail={
                        "tokens_consumed": tokens,
                        "dollars_consumed": round(dollars, 6),
                        "tokens_cap": agent.tokens_cap,
                        "dollars_cap": agent.dollars_cap,
                    },
                )
            )
    return events


def detect_loop_patterns(session: Session, now: datetime) -> list[PauseEvent]:
    del now
    events: list[PauseEvent] = []
    agents = session.exec(select(Agent)).all()
    for agent in agents:
        recent = session.exec(
            select(Proposal)
            .where(Proposal.agent_id == agent.agent_id)
            .order_by(Proposal.submitted_at.desc())  # type: ignore[attr-defined]
            .limit(LOOP_DETECTION_WINDOW)
        ).all()
        if len(recent) < LOOP_DETECTION_WINDOW:
            continue
        hashes = {p.payload_hash for p in recent}
        if len(hashes) == 1:
            events.append(
                PauseEvent(
                    agent_id=agent.agent_id,
                    reason="loop_pattern",
                    detail={
                        "payload_hash": next(iter(hashes)),
                        "window": LOOP_DETECTION_WINDOW,
                        "proposal_ids": [p.proposal_id for p in recent],
                    },
                )
            )
    return events


def pause_agent(session: Session, *, agent_id: str, reason: str, detail: dict) -> bool:
    """Idempotently flip an agent to paused. Returns True if state changed."""
    agent = session.get(Agent, agent_id)
    if agent is None:
        # Auto-create — keeps watchdog tolerant of new agent ids that the
        # orchestrator may have seen before us.
        agent = Agent(agent_id=agent_id, status="paused", paused_reason=reason)
        session.add(agent)
        record_audit(
            session,
            event_type="watchdog_pause",
            agent_id=agent_id,
            payload={"reason": reason, **detail},
        )
        return True
    if agent.status == "paused":
        return False
    agent.status = "paused"
    agent.paused_reason = reason
    session.add(agent)
    record_audit(
        session,
        event_type="watchdog_pause",
        agent_id=agent_id,
        payload={"reason": reason, **detail},
    )
    return True


def run_one_pass(session: Session, now: datetime | None = None) -> list[PauseEvent]:
    """One sweep of all detection rules. Returns the events fired this pass."""
    now = now or utcnow()
    events: list[PauseEvent] = []
    events += detect_stuck_tasks(session, now)
    events += detect_budget_breaches(session, now)
    events += detect_loop_patterns(session, now)
    for e in events:
        pause_agent(session, agent_id=e.agent_id, reason=e.reason, detail=e.detail)
    session.commit()
    if events:
        log.info(
            "watchdog_pass",
            paused_count=len(events),
            agents=[e.agent_id for e in events],
        )
    return events


_should_stop = False


def _handle_sigterm(signum: int, frame: object) -> None:  # pragma: no cover
    del signum, frame
    global _should_stop
    _should_stop = True
    log.info("watchdog_sigterm_received")


def loop() -> None:  # pragma: no cover
    """Blocking poll loop. Used by `python -m infra.watchdog`."""
    init_db()
    signal.signal(signal.SIGTERM, _handle_sigterm)
    signal.signal(signal.SIGINT, _handle_sigterm)
    log.info("watchdog_started", interval_seconds=WATCHDOG_INTERVAL_SECONDS)
    while not _should_stop:
        try:
            with Session(engine) as session:
                run_one_pass(session)
        except Exception as exc:
            log.error("watchdog_pass_failed", error=str(exc))
        # Sleep in 1s slices so SIGTERM is responsive.
        for _ in range(WATCHDOG_INTERVAL_SECONDS):
            if _should_stop:
                break
            time.sleep(1)
    log.info("watchdog_stopped")


if __name__ == "__main__":  # pragma: no cover
    loop()
    sys.exit(0)
