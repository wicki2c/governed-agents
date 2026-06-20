"""Shared pytest fixtures.

Strategy: each test gets its own in-memory SQLite engine (StaticPool so all
connections share one in-process DB). We monkeypatch infra.db.engine and
infra.watchdog.engine so any code path that references the module-level
engine sees the test instance. We also redirect AUDIT_JSONL_PATH to a
tmp file for the duration of the test.

For HTTP tests, FastAPI's dependency_overrides points get_session at the
same test engine.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

import infra.app
import infra.db
import infra.watchdog
from infra.db import KNOWN_AGENT_IDS, Agent


@pytest.fixture
def test_engine(monkeypatch, tmp_path):
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)
    monkeypatch.setattr(infra.db, "engine", engine)
    monkeypatch.setattr(infra.watchdog, "engine", engine)

    audit_path = tmp_path / "audit.jsonl"
    audit_path.touch()
    monkeypatch.setattr(infra.db, "AUDIT_JSONL_PATH", audit_path)
    monkeypatch.setattr(infra.app, "AUDIT_JSONL_PATH", audit_path)
    return engine


@pytest.fixture
def session(test_engine) -> Iterator[Session]:
    with Session(test_engine) as s:
        for agent_id in KNOWN_AGENT_IDS:
            s.add(Agent(agent_id=agent_id))
        s.commit()
        yield s


@pytest.fixture
def client(test_engine, session, monkeypatch) -> Iterator[TestClient]:
    # Lifespan would call the real init_db (which writes to the on-disk
    # path). Replace it with a no-op since tables are already created on
    # the test engine.
    monkeypatch.setattr(infra.app, "init_db", lambda: None)

    def _override_session() -> Iterator[Session]:
        with Session(test_engine) as s:
            yield s

    infra.app.app.dependency_overrides[infra.db.get_session] = _override_session
    try:
        with TestClient(infra.app.app) as c:
            yield c
    finally:
        infra.app.app.dependency_overrides.clear()


@pytest.fixture
def now() -> datetime:
    return datetime.now(UTC)


def _proposal_body(
    *,
    proposal_id: str,
    agent_id: str = "noop",
    action_type: str = "publish_content",
    requires_approval: bool = True,
    reversible: bool = True,
    payload: dict | None = None,
    wave: int = 1,
) -> dict:
    """Helper used by several tests to build a valid POST /proposals body."""
    now_iso = datetime.now(UTC)
    return {
        "proposal_id": proposal_id,
        "agent_id": agent_id,
        "wave": wave,
        "submitted_at": now_iso.isoformat(),
        "action_type": action_type,
        "payload": payload or {"topic": "test"},
        "expected_outcome": "test outcome",
        "expected_cost_tokens": 0,
        "expected_cost_dollars": 0.0,
        "reversible": reversible,
        "requires_approval": requires_approval,
        "expires_at": (now_iso + timedelta(hours=1)).isoformat(),
        "rationale": "test rationale",
        "links": [],
    }


@pytest.fixture
def proposal_body():
    return _proposal_body
