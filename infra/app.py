"""FastAPI orchestration server.

Endpoints expose proposals, budgets, audit log, scoreboard, status, admin,
and credentials-proxy operations. Binds to 127.0.0.1 only and has no auth
— localhost-only access is the auth. Do NOT expose this port to a network.

The proposal-gate is enforced server-side. Agents cannot self-approve
actions on the never_auto_approve list in proposal_policy.toml; attempts
are returned 400 and recorded as audit_flag events.
"""

from __future__ import annotations

import hashlib
import json
import os
import tomllib
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path
from typing import Annotated, Any, Literal

import structlog
from fastapi import Depends, FastAPI, HTTPException, Query
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, field_validator
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, func, select

# `decision_hooks` is the post-approval dispatch table. Hook plugins
# register their handlers against it (see infra/decision_hooks.py and the
# worked example in the docs). The core ships with no hooks registered, so
# `dispatch()` is a no-op until a deployment adds one.
from infra import credentials_proxy, decision_hooks
from infra.db import (
    AUDIT_JSONL_PATH,
    DEFAULT_DOLLARS_PER_1K_TOKENS,
    KNOWN_AGENT_IDS,
    Agent,
    AuditLog,
    BudgetLedger,
    Proposal,
    get_session,
    init_db,
    record_audit,
    utcnow,
)

# ---------------------------------------------------------------------------
# Localhost-only invariant. Imported by scripts/start_*.sh and
# asserted in tests/test_bind.py. ORCHESTRATOR_HOST is intentionally NOT
# read from env — the localhost bind is a non-negotiable, not a config.
# ---------------------------------------------------------------------------
UVICORN_HOST = "127.0.0.1"
UVICORN_PORT = int(os.environ.get("ORCHESTRATOR_PORT", "8005"))

POLICY_PATH = Path(__file__).resolve().parent / "proposal_policy.toml"
STATIC_DIR = Path(__file__).resolve().parent / "static"
DASHBOARD_HTML = Path(__file__).resolve().parent / "dashboard.html"

log = structlog.get_logger(__name__)

STARTED_AT: datetime = utcnow()


# ---------------------------------------------------------------------------
# Proposal policy
# ---------------------------------------------------------------------------


class ProposalPolicy(BaseModel):
    auto_approve: set[str]
    never_auto_approve: set[str]
    default_requires_approval: bool

    @classmethod
    def load(cls, path: Path = POLICY_PATH) -> ProposalPolicy:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
        return cls(
            auto_approve=set(data.get("auto_approve", {}).get("action_types", [])),
            never_auto_approve=set(data.get("never_auto_approve", {}).get("action_types", [])),
            default_requires_approval=bool(data.get("defaults", {}).get("requires_approval", True)),
        )


# ---------------------------------------------------------------------------
# Pydantic request/response models
# ---------------------------------------------------------------------------


class ProposalSubmit(BaseModel):
    # Optional: when omitted, the server generates a UUIDv4 and returns it
    # in the response. Client-provided IDs are still accepted for back-compat.
    # Uniqueness is enforced by the DB primary key either way;
    # collisions return 409.
    proposal_id: str | None = None
    agent_id: str
    wave: int
    submitted_at: datetime
    action_type: str
    payload: dict[str, Any]
    expected_outcome: str
    expected_cost_tokens: int = 0
    expected_cost_dollars: float = 0.0
    reversible: bool = True
    requires_approval: bool = True
    expires_at: datetime
    rationale: str = ""
    links: list[str] = Field(default_factory=list)

    @field_validator("agent_id")
    @classmethod
    def _agent_id_nonempty(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("agent_id must be non-empty")
        return v


class ProposalSubmitResponse(BaseModel):
    proposal_id: str
    status: str
    review_url: str


class ProposalDecision(BaseModel):
    decision: Literal["approve", "reject"]
    final: bool = False
    note: str | None = None


class BudgetCharge(BaseModel):
    tokens: int
    tool: str
    task_id: str | None = None
    dollars: float | None = None


class BudgetResponse(BaseModel):
    agent_id: str
    wave: int
    tokens_consumed: int
    tokens_cap: int
    dollars_consumed: float
    dollars_cap: float
    status: str
    remaining_pct: float


class AuditSubmit(BaseModel):
    agent_id: str | None = None
    event_type: str
    proposal_id: str | None = None
    task_id: str | None = None
    payload: dict[str, Any] = Field(default_factory=dict)


class AdminPauseRequest(BaseModel):
    reason: str = "manual_pause_all"


class CredentialsIssueRequest(BaseModel):
    agent_id: str
    scope: str
    ttl_seconds: int = 300


class CredentialsRedeemRequest(BaseModel):
    token: str


# ---------------------------------------------------------------------------
# App factory + lifespan
# ---------------------------------------------------------------------------


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    init_db()
    app.state.policy = ProposalPolicy.load()  # type: ignore[attr-defined]
    log.info(
        "orchestrator_started",
        host=UVICORN_HOST,
        auto_approve=sorted(app.state.policy.auto_approve),  # type: ignore[attr-defined]
        never_auto_approve=sorted(app.state.policy.never_auto_approve),  # type: ignore[attr-defined]
    )
    yield


app = FastAPI(
    title="Governed Agents Orchestrator",
    version="0.1.0",
    lifespan=lifespan,
)

if STATIC_DIR.exists():
    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


SessionDep = Annotated[Session, Depends(get_session)]


def get_policy() -> ProposalPolicy:
    return app.state.policy  # type: ignore[attr-defined,no-any-return]


PolicyDep = Annotated[ProposalPolicy, Depends(get_policy)]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _payload_hash(payload: dict[str, Any]) -> str:
    canonical = json.dumps(payload, sort_keys=True, default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _proposal_to_dict(p: Proposal) -> dict[str, Any]:
    return {
        "proposal_id": p.proposal_id,
        "agent_id": p.agent_id,
        "wave": p.wave,
        "submitted_at": p.submitted_at.isoformat(),
        "action_type": p.action_type,
        "payload": json.loads(p.payload),
        "payload_hash": p.payload_hash,
        "expected_outcome": p.expected_outcome,
        "expected_cost_tokens": p.expected_cost_tokens,
        "expected_cost_dollars": p.expected_cost_dollars,
        "reversible": p.reversible,
        "requires_approval": p.requires_approval,
        "expires_at": p.expires_at.isoformat(),
        "rationale": p.rationale,
        "links": json.loads(p.links),
        "status": p.status,
        "decision": p.decision,
        "decided_by": p.decided_by,
        "decided_at": p.decided_at.isoformat() if p.decided_at else None,
        "decision_final": p.decision_final,
    }


def _ensure_agent_row(session: Session, agent_id: str) -> Agent:
    agent = session.get(Agent, agent_id)
    if agent is None:
        agent = Agent(agent_id=agent_id)
        session.add(agent)
        session.flush()
    return agent


def _budget_for(session: Session, agent_id: str, wave: int = 0) -> BudgetResponse:
    agent = session.get(Agent, agent_id)
    if agent is None:
        raise HTTPException(status_code=404, detail=f"unknown agent_id: {agent_id}")
    totals = session.exec(
        select(
            func.coalesce(func.sum(BudgetLedger.tokens), 0),
            func.coalesce(func.sum(BudgetLedger.dollars), 0.0),
        ).where(BudgetLedger.agent_id == agent_id)
    ).one()
    tokens_consumed = int(totals[0] or 0)
    dollars_consumed = float(totals[1] or 0.0)
    cap_basis = max(agent.dollars_cap, 1e-9)
    remaining_pct = max(0.0, (1.0 - dollars_consumed / cap_basis) * 100.0)
    return BudgetResponse(
        agent_id=agent_id,
        wave=wave,
        tokens_consumed=tokens_consumed,
        tokens_cap=agent.tokens_cap,
        dollars_consumed=round(dollars_consumed, 6),
        dollars_cap=agent.dollars_cap,
        status=agent.status,
        remaining_pct=round(remaining_pct, 2),
    )


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@app.get("/")
def dashboard() -> FileResponse:
    return FileResponse(DASHBOARD_HTML)


@app.post("/proposals", response_model=ProposalSubmitResponse)
def submit_proposal(
    submission: ProposalSubmit,
    session: SessionDep,
    policy: PolicyDep,
) -> ProposalSubmitResponse:
    # Enforce the proposal-gate: never let an agent self-approve a
    # never_auto_approve action_type. Log and reject. We deliberately log
    # what the agent sent (submission.proposal_id, possibly null) rather
    # than a freshly-minted id — the audit row is evidence of what the
    # malformed client posted.
    if (
        submission.action_type in policy.never_auto_approve
        and submission.requires_approval is False
    ):
        record_audit(
            session,
            event_type="audit_flag_self_approve_attempt",
            agent_id=submission.agent_id,
            proposal_id=submission.proposal_id,
            payload={
                "action_type": submission.action_type,
                "reason": "requires_approval=false on never_auto_approve list",
            },
        )
        session.commit()
        raise HTTPException(
            status_code=400,
            detail={
                "error": "self_approve_blocked",
                "action_type": submission.action_type,
                "message": (
                    f"action_type {submission.action_type!r} requires human approval; "
                    "cannot self-approve"
                ),
                "audit_flagged": True,
            },
        )

    # Server generates proposal_id when the client omits it.
    proposal_id = submission.proposal_id or str(uuid.uuid4())

    requires_approval = submission.requires_approval
    # Coerce irreversible proposals to requires_approval=true regardless.
    if submission.reversible is False:
        requires_approval = True

    if (
        submission.action_type in policy.auto_approve
        and requires_approval is False
        and submission.reversible is True
    ):
        status = "auto-approved"
        decided_by: str | None = "auto_policy"
        decided_at: datetime | None = utcnow()
    else:
        # Anything that did not opt-out via the auto-approve path waits
        # for a human, regardless of the agent's submitted preference.
        status = "pending"
        decided_by = None
        decided_at = None
        requires_approval = True

    _ensure_agent_row(session, submission.agent_id)

    proposal = Proposal(
        proposal_id=proposal_id,
        agent_id=submission.agent_id,
        wave=submission.wave,
        submitted_at=submission.submitted_at,
        action_type=submission.action_type,
        payload=json.dumps(submission.payload, default=str, sort_keys=True),
        payload_hash=_payload_hash(submission.payload),
        expected_outcome=submission.expected_outcome,
        expected_cost_tokens=submission.expected_cost_tokens,
        expected_cost_dollars=submission.expected_cost_dollars,
        reversible=submission.reversible,
        requires_approval=requires_approval,
        expires_at=submission.expires_at,
        rationale=submission.rationale,
        links=json.dumps(submission.links),
        status=status,
        decided_by=decided_by,
        decided_at=decided_at,
    )
    session.add(proposal)
    try:
        session.flush()
    except IntegrityError:
        session.rollback()
        raise HTTPException(
            status_code=409,
            detail=f"proposal_id {proposal_id!r} already exists",
        ) from None

    record_audit(
        session,
        event_type="proposal_submitted",
        agent_id=submission.agent_id,
        proposal_id=proposal_id,
        payload={
            "action_type": submission.action_type,
            "status": status,
            "wave": submission.wave,
        },
    )

    agent = session.get(Agent, submission.agent_id)
    if agent is not None:
        agent.last_seen = utcnow()
        session.add(agent)

    session.commit()

    return ProposalSubmitResponse(
        proposal_id=proposal_id,
        status=status,
        review_url=(f"http://{UVICORN_HOST}:{UVICORN_PORT}/#proposal-{proposal_id}"),
    )


@app.get("/proposals")
def list_proposals(
    session: SessionDep,
    status: str | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=500),
) -> list[dict[str, Any]]:
    stmt = select(Proposal)
    if status and status != "all":
        stmt = stmt.where(Proposal.status == status)
    stmt = stmt.order_by(Proposal.submitted_at.desc()).limit(limit)  # type: ignore[attr-defined]
    rows = session.exec(stmt).all()
    return [_proposal_to_dict(r) for r in rows]


@app.get("/proposals/{proposal_id}")
def get_proposal(proposal_id: str, session: SessionDep) -> dict[str, Any]:
    proposal = session.get(Proposal, proposal_id)
    if proposal is None:
        raise HTTPException(status_code=404, detail="proposal not found")
    return _proposal_to_dict(proposal)


@app.post("/proposals/{proposal_id}/decision")
def decide_proposal(
    proposal_id: str,
    decision: ProposalDecision,
    session: SessionDep,
) -> dict[str, Any]:
    proposal = session.get(Proposal, proposal_id)
    if proposal is None:
        raise HTTPException(status_code=404, detail="proposal not found")
    if proposal.status not in ("pending",):
        raise HTTPException(
            status_code=400,
            detail=f"proposal {proposal_id!r} already decided (status={proposal.status})",
        )
    proposal.status = "approved" if decision.decision == "approve" else "rejected"
    proposal.decision = decision.note or decision.decision
    proposal.decided_by = "human"
    proposal.decided_at = utcnow()
    proposal.decision_final = decision.final
    session.add(proposal)

    record_audit(
        session,
        event_type="proposal_decided",
        agent_id=proposal.agent_id,
        proposal_id=proposal_id,
        payload={
            "decision": decision.decision,
            "final": decision.final,
            "note": decision.note,
        },
    )
    session.commit()

    # Post-approval decision hook. Single dispatch site for any side
    # effect that should run when a proposal is approved (publish a file,
    # call an external API, deploy a service, ...). A hook plugin registers
    # its `action_type` → handler mapping with `decision_hooks.register`.
    # The load-bearing invariant — a hook failure must NEVER poison the
    # decision — is preserved by:
    #   1. The decision is committed above before this dispatch fires.
    #   2. The dispatch is wrapped in try/except; on any exception we
    #      write `audit_flag_decision_hook_error` carrying the
    #      action_type + exception string and return the same 200 we
    #      would have without the hook.
    #   3. `decision_hooks.dispatch` returns None (no-op) for any
    #      action_type without a registered handler — the common case.
    if proposal.status == "approved":
        try:
            result = decision_hooks.dispatch(proposal, session)
            if result is not None:
                session.commit()
        except Exception as exc:
            log.error(
                "decision_hook_error",
                proposal_id=proposal_id,
                action_type=proposal.action_type,
                error=str(exc),
            )
            session.rollback()
            try:
                record_audit(
                    session,
                    event_type="audit_flag_decision_hook_error",
                    agent_id=proposal.agent_id,
                    proposal_id=proposal_id,
                    payload={
                        "error": str(exc),
                        "action_type": proposal.action_type,
                    },
                )
                session.commit()
            except Exception as inner_exc:
                log.error(
                    "decision_hook_error_audit_failed",
                    proposal_id=proposal_id,
                    error=str(inner_exc),
                )
                session.rollback()

    return {
        "proposal_id": proposal_id,
        "status": proposal.status,
        "decided_at": proposal.decided_at.isoformat() if proposal.decided_at else None,
    }


@app.get("/budget/{agent_id}", response_model=BudgetResponse)
def get_budget(
    agent_id: str,
    session: SessionDep,
    wave: int = Query(default=0, ge=0),
) -> BudgetResponse:
    return _budget_for(session, agent_id, wave=wave)


@app.post("/budget/{agent_id}/charge", response_model=BudgetResponse)
def charge_budget(
    agent_id: str,
    charge: BudgetCharge,
    session: SessionDep,
) -> BudgetResponse:
    agent = session.get(Agent, agent_id)
    if agent is None:
        raise HTTPException(status_code=404, detail=f"unknown agent_id: {agent_id}")
    dollars = (
        charge.dollars
        if charge.dollars is not None
        else (charge.tokens / 1000.0) * DEFAULT_DOLLARS_PER_1K_TOKENS
    )
    session.add(
        BudgetLedger(
            agent_id=agent_id,
            tokens=charge.tokens,
            dollars=dollars,
            tool=charge.tool,
            task_id=charge.task_id,
        )
    )
    record_audit(
        session,
        event_type="budget_charge",
        agent_id=agent_id,
        task_id=charge.task_id,
        payload={"tokens": charge.tokens, "dollars": dollars, "tool": charge.tool},
    )
    session.flush()

    snapshot = _budget_for(session, agent_id)
    over_dollars = snapshot.dollars_consumed > agent.dollars_cap
    over_tokens = snapshot.tokens_consumed > agent.tokens_cap
    if (over_dollars or over_tokens) and agent.status != "paused":
        agent.status = "paused"
        agent.paused_reason = "budget_cap_exceeded"
        session.add(agent)
        record_audit(
            session,
            event_type="budget_paused",
            agent_id=agent_id,
            payload={
                "tokens_consumed": snapshot.tokens_consumed,
                "dollars_consumed": snapshot.dollars_consumed,
                "tokens_cap": agent.tokens_cap,
                "dollars_cap": agent.dollars_cap,
            },
        )
    elif snapshot.remaining_pct < 25 and agent.status == "ok":
        agent.status = "warning"
        session.add(agent)
    session.commit()
    return _budget_for(session, agent_id, wave=0)


@app.post("/audit")
def post_audit(submission: AuditSubmit, session: SessionDep) -> dict[str, Any]:
    row = record_audit(
        session,
        event_type=submission.event_type,
        agent_id=submission.agent_id,
        proposal_id=submission.proposal_id,
        task_id=submission.task_id,
        payload=submission.payload,
    )
    session.commit()
    return {"audit_id": row.audit_id, "ts": row.ts.isoformat()}


@app.get("/audit")
def list_audit(
    session: SessionDep,
    limit: int = Query(default=50, ge=1, le=500),
    agent_id: str | None = None,
    event_type: str | None = None,
) -> list[dict[str, Any]]:
    stmt = select(AuditLog).order_by(AuditLog.ts.desc()).limit(limit)  # type: ignore[attr-defined]
    if agent_id:
        stmt = stmt.where(AuditLog.agent_id == agent_id)
    if event_type:
        stmt = stmt.where(AuditLog.event_type == event_type)
    rows = session.exec(stmt).all()
    return [
        {
            "audit_id": r.audit_id,
            "ts": r.ts.isoformat(),
            "agent_id": r.agent_id,
            "event_type": r.event_type,
            "proposal_id": r.proposal_id,
            "task_id": r.task_id,
            "payload": json.loads(r.payload) if r.payload else {},
        }
        for r in rows
    ]


@app.get("/scoreboard")
def scoreboard(session: SessionDep) -> list[dict[str, Any]]:
    agents = session.exec(select(Agent).order_by(Agent.agent_id)).all()  # type: ignore[arg-type]
    result: list[dict[str, Any]] = []
    for agent in agents:
        prop_counts = dict(
            session.exec(
                select(Proposal.status, func.count())  # type: ignore[arg-type]
                .where(Proposal.agent_id == agent.agent_id)
                .group_by(Proposal.status)
            ).all()
        )
        totals = session.exec(
            select(
                func.coalesce(func.sum(BudgetLedger.tokens), 0),
                func.coalesce(func.sum(BudgetLedger.dollars), 0.0),
            ).where(BudgetLedger.agent_id == agent.agent_id)
        ).one()
        result.append(
            {
                "agent_id": agent.agent_id,
                "status": agent.status,
                "proposals_submitted": int(sum(prop_counts.values())),
                "proposals_approved": int(prop_counts.get("approved", 0))
                + int(prop_counts.get("auto-approved", 0)),
                "proposals_rejected": int(prop_counts.get("rejected", 0)),
                "tokens_consumed": int(totals[0] or 0),
                "dollars_consumed": round(float(totals[1] or 0.0), 6),
                "revenue_events_count": 0,
            }
        )
    return result


@app.get("/status")
def status(session: SessionDep) -> dict[str, Any]:
    db_ok = True
    paused: list[str] = []
    try:
        paused = [
            a.agent_id for a in session.exec(select(Agent).where(Agent.status == "paused")).all()
        ]
    except Exception:
        db_ok = False

    audit_jsonl_ok = AUDIT_JSONL_PATH.exists() and AUDIT_JSONL_PATH.is_file()
    return {
        "ok": db_ok and audit_jsonl_ok,
        "host": UVICORN_HOST,
        "port": UVICORN_PORT,
        "db_ok": db_ok,
        "audit_jsonl_ok": audit_jsonl_ok,
        "started_at": STARTED_AT.isoformat(),
        "agents_paused": paused,
        "known_agents": list(KNOWN_AGENT_IDS),
    }


@app.get("/status/me")
def status_me(
    session: SessionDep,
    agent_id: str = Query(...),
) -> dict[str, Any]:
    agent = session.get(Agent, agent_id)
    if agent is None:
        raise HTTPException(status_code=404, detail=f"unknown agent_id: {agent_id}")
    agent.last_seen = utcnow()
    session.add(agent)
    session.commit()
    return {
        "agent_id": agent_id,
        "status": agent.status,
        "paused_reason": agent.paused_reason,
    }


@app.post("/admin/pause-all")
def admin_pause_all(req: AdminPauseRequest, session: SessionDep) -> dict[str, Any]:
    agents = session.exec(select(Agent)).all()
    paused_now: list[str] = []
    for agent in agents:
        if agent.status != "paused":
            agent.status = "paused"
            agent.paused_reason = req.reason
            session.add(agent)
            paused_now.append(agent.agent_id)
    record_audit(
        session,
        event_type="admin_pause_all",
        payload={"reason": req.reason, "paused_agents": paused_now},
    )
    session.commit()
    return {"paused_agents": paused_now}


@app.post("/credentials/issue")
def credentials_issue(req: CredentialsIssueRequest, session: SessionDep) -> dict[str, Any]:
    _ensure_agent_row(session, req.agent_id)
    token, row = credentials_proxy.issue_token(
        session,
        agent_id=req.agent_id,
        scope=req.scope,
        ttl_seconds=req.ttl_seconds,
    )
    record_audit(
        session,
        event_type="credentials_issued",
        agent_id=req.agent_id,
        payload={"scope": req.scope, "expires_at": row.expires_at.isoformat()},
    )
    session.commit()
    return {"token": token, "expires_at": row.expires_at.isoformat()}


@app.post("/credentials/redeem")
def credentials_redeem(req: CredentialsRedeemRequest, session: SessionDep) -> JSONResponse:
    result = credentials_proxy.redeem_token(session, req.token, now=utcnow())
    if result.status == "expired":
        record_audit(
            session,
            event_type="credentials_expired",
            payload={"reason": "token expired"},
        )
        session.commit()
        return JSONResponse(status_code=401, content={"detail": "token expired"})
    if result.status == "already_used":
        record_audit(
            session,
            event_type="credentials_replay_blocked",
            agent_id=result.agent_id,
            payload={"scope": result.scope},
        )
        session.commit()
        return JSONResponse(status_code=401, content={"detail": "token already used"})
    if result.status == "not_found":
        return JSONResponse(status_code=401, content={"detail": "unknown token"})

    record_audit(
        session,
        event_type="credentials_redeemed",
        agent_id=result.agent_id,
        payload={"scope": result.scope},
    )
    session.commit()
    return JSONResponse(
        status_code=200,
        content={"scope": result.scope, "agent_id": result.agent_id},
    )


__all__ = ["STARTED_AT", "UVICORN_HOST", "UVICORN_PORT", "ProposalPolicy", "app"]


if __name__ == "__main__":  # pragma: no cover
    import uvicorn

    uvicorn.run("infra.app:app", host=UVICORN_HOST, port=UVICORN_PORT, reload=False)
