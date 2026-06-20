"""SQLModel schemas and engine for the orchestration server.

Tables: proposals, budget_ledger, audit_log, agents, one_shot_tokens.
Audit writes are dual-sinked to an append-only JSONL mirror so the trail
survives DB corruption (path: AUDIT_JSONL_PATH).
"""

from __future__ import annotations

import json
import os
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import event
from sqlalchemy.engine import Engine
from sqlmodel import Field, Session, SQLModel, create_engine

DEFAULT_DB_PATH = Path(__file__).resolve().parent / "db.sqlite"
DEFAULT_AUDIT_JSONL_PATH = Path(__file__).resolve().parent / "audit.jsonl"

DB_URL = os.environ.get("ORCHESTRATOR_DB_URL", f"sqlite:///{DEFAULT_DB_PATH}")
AUDIT_JSONL_PATH = Path(os.environ.get("AUDIT_JSONL_PATH", str(DEFAULT_AUDIT_JSONL_PATH)))

# Agent ids the orchestrator pre-seeds a ledger row for at startup. Add
# your own agents here, or let the server auto-create a row on first
# contact (any unknown agent_id that submits a proposal gets one on the
# fly). `noop` is the bundled smoke / demo agent; `orchestrator` is the
# runner that invokes agents.
KNOWN_AGENT_IDS: tuple[str, ...] = (
    "noop",
    "orchestrator",
)

# Optional per-agent budget caps applied by `init_db()` when it seeds a
# NEW Agent row. Any agent_id not listed here falls back to the schema
# defaults on the `Agent` model (`tokens_cap` / `dollars_cap`). Existing
# rows are never retroactively updated — set a cap before an agent's
# first run, or update the row directly.
#
# Each value is `(tokens_cap, dollars_cap)`, e.g.:
#     AGENT_CAPS = {"my-agent": (10_000_000, 25.00)}
AGENT_CAPS: dict[str, tuple[int, float]] = {}

# Fallback token-to-dollar conversion, used when a budget charge omits an
# explicit `dollars` amount. Override to match your model's pricing.
DEFAULT_DOLLARS_PER_1K_TOKENS = 0.015

engine = create_engine(
    DB_URL,
    connect_args={"check_same_thread": False} if DB_URL.startswith("sqlite") else {},
    echo=False,
)


@event.listens_for(Engine, "connect")
def _sqlite_pragma(dbapi_connection: Any, _: Any) -> None:
    """Enable WAL + foreign keys on every SQLite connection."""
    if dbapi_connection.__class__.__module__.startswith("sqlite3"):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()


def utcnow() -> datetime:
    return datetime.now(UTC)


class Proposal(SQLModel, table=True):
    __tablename__ = "proposals"

    proposal_id: str = Field(primary_key=True)
    agent_id: str = Field(foreign_key="agents.agent_id", index=True)
    wave: int
    submitted_at: datetime = Field(default_factory=utcnow, index=True)
    action_type: str = Field(index=True)
    payload: str  # JSON-encoded
    payload_hash: str = Field(index=True)
    expected_outcome: str
    expected_cost_tokens: int = 0
    expected_cost_dollars: float = 0.0
    reversible: bool = True
    requires_approval: bool = True
    expires_at: datetime
    rationale: str = ""
    links: str = "[]"  # JSON-encoded list[str]
    status: str = Field(default="pending", index=True)
    decision: str | None = None
    decided_by: str | None = None
    decided_at: datetime | None = None
    decision_final: bool = False


class BudgetLedger(SQLModel, table=True):
    __tablename__ = "budget_ledger"

    id: int | None = Field(default=None, primary_key=True)
    agent_id: str = Field(foreign_key="agents.agent_id", index=True)
    ts: datetime = Field(default_factory=utcnow, index=True)
    tokens: int
    dollars: float
    tool: str
    task_id: str | None = None


class AuditLog(SQLModel, table=True):
    __tablename__ = "audit_log"

    audit_id: int | None = Field(default=None, primary_key=True)
    ts: datetime = Field(default_factory=utcnow, index=True)
    agent_id: str | None = None
    event_type: str = Field(index=True)
    proposal_id: str | None = None
    task_id: str | None = None
    payload: str = "{}"  # JSON-encoded


class Agent(SQLModel, table=True):
    __tablename__ = "agents"

    agent_id: str = Field(primary_key=True)
    status: str = Field(default="ok")  # ok | warning | paused
    paused_reason: str | None = None
    last_seen: datetime | None = None
    tokens_cap: int = 200_000
    dollars_cap: float = 50.00
    created_at: datetime = Field(default_factory=utcnow)


class OneShotToken(SQLModel, table=True):
    __tablename__ = "one_shot_tokens"

    id: int | None = Field(default=None, primary_key=True)
    token_hash: str = Field(unique=True, index=True)
    agent_id: str = Field(foreign_key="agents.agent_id")
    scope: str
    issued_at: datetime = Field(default_factory=utcnow)
    expires_at: datetime
    used_at: datetime | None = None


def init_db() -> None:
    """Create tables and seed the known agent rows.

    New rows pick up any per-agent caps from `AGENT_CAPS`; existing rows
    are never retroactively updated.
    """
    SQLModel.metadata.create_all(engine)
    AUDIT_JSONL_PATH.parent.mkdir(parents=True, exist_ok=True)
    AUDIT_JSONL_PATH.touch(exist_ok=True)
    with Session(engine) as session:
        for agent_id in KNOWN_AGENT_IDS:
            if session.get(Agent, agent_id) is None:
                caps = AGENT_CAPS.get(agent_id)
                if caps is not None:
                    tokens_cap, dollars_cap = caps
                    session.add(
                        Agent(
                            agent_id=agent_id,
                            tokens_cap=tokens_cap,
                            dollars_cap=dollars_cap,
                        )
                    )
                else:
                    session.add(Agent(agent_id=agent_id))
        session.commit()


def get_session() -> Iterator[Session]:
    """FastAPI dependency. Yields a session, closes on exit."""
    with Session(engine) as session:
        yield session


def record_audit(
    session: Session,
    *,
    event_type: str,
    agent_id: str | None = None,
    proposal_id: str | None = None,
    task_id: str | None = None,
    payload: dict[str, Any] | None = None,
) -> AuditLog:
    """Insert an AuditLog row and append the same event to audit.jsonl.

    The jsonl append is best-effort. If it fails we surface the error via
    /status (the FastAPI app reads AUDIT_JSONL_PATH.exists() + writability)
    but still commit the SQLite row — the SQL table is authoritative.
    """
    payload_json = json.dumps(payload or {}, default=str, sort_keys=True)
    row = AuditLog(
        event_type=event_type,
        agent_id=agent_id,
        proposal_id=proposal_id,
        task_id=task_id,
        payload=payload_json,
    )
    session.add(row)
    session.flush()  # populate audit_id and ts for the jsonl mirror

    record = {
        "audit_id": row.audit_id,
        "ts": row.ts.isoformat(),
        "agent_id": row.agent_id,
        "event_type": row.event_type,
        "proposal_id": row.proposal_id,
        "task_id": row.task_id,
        "payload": payload or {},
    }
    try:
        with AUDIT_JSONL_PATH.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record, default=str) + "\n")
    except OSError:
        # Don't fail the SQL transaction over a jsonl mirror miss.
        # /status surfaces this via audit_jsonl_ok=False.
        pass

    return row


def reset_engine_for_tests(new_engine: Engine) -> None:
    """Swap the module-level engine. Used only by tests via conftest."""
    global engine
    engine = new_engine
