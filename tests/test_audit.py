"""Audit log dual-write: SQLite + infra/audit.jsonl."""

from __future__ import annotations

import json

from sqlmodel import select

from infra.db import AUDIT_JSONL_PATH, AuditLog


def test_audit_writes_to_sqlite_and_jsonl(client, session):
    """Every audit row appears in BOTH the SQLite audit_log table AND the
    append-only infra/audit.jsonl mirror.
    """
    response = client.post(
        "/audit",
        json={
            "agent_id": "noop",
            "event_type": "manual_test_event",
            "payload": {"hello": "world"},
        },
    )
    assert response.status_code == 200, response.text

    # SQLite side
    rows = session.exec(select(AuditLog).where(AuditLog.event_type == "manual_test_event")).all()
    assert len(rows) == 1
    assert rows[0].agent_id == "noop"

    # JSONL side
    import infra.db as db_mod  # re-read after monkeypatch

    text = db_mod.AUDIT_JSONL_PATH.read_text(encoding="utf-8")
    lines = [ln for ln in text.splitlines() if ln.strip()]
    matching = [json.loads(ln) for ln in lines if "manual_test_event" in ln]
    assert len(matching) == 1
    assert matching[0]["payload"] == {"hello": "world"}


def test_audit_jsonl_survives_orchestrator_restart(client):
    """The jsonl mirror is append-only; existing content must not be
    truncated when a new audit row is written.
    """
    client.post("/audit", json={"event_type": "first_event"})
    client.post("/audit", json={"event_type": "second_event"})

    import infra.db as db_mod

    lines = [
        ln for ln in db_mod.AUDIT_JSONL_PATH.read_text(encoding="utf-8").splitlines() if ln.strip()
    ]
    event_types = [json.loads(ln)["event_type"] for ln in lines]
    assert "first_event" in event_types
    assert "second_event" in event_types
    # second event hasn't truncated the first
    assert event_types.index("first_event") < event_types.index("second_event")


def test_audit_list_endpoint_filters_by_event_type(client):
    client.post("/audit", json={"event_type": "alpha"})
    client.post("/audit", json={"event_type": "beta"})

    response = client.get("/audit?event_type=alpha&limit=10")
    assert response.status_code == 200, response.text
    rows = response.json()
    assert all(r["event_type"] == "alpha" for r in rows)
    assert any(r["event_type"] == "alpha" for r in rows)


def test_audit_jsonl_path_redirected_to_tmp(test_engine, tmp_path):
    """The conftest monkeypatch puts AUDIT_JSONL_PATH under pytest's tmp_path,
    not the on-disk infra/audit.jsonl. This guards against an accidental
    revert that would have test runs polluting the real audit log.
    """
    import infra.db as db_mod

    assert db_mod.AUDIT_JSONL_PATH.is_relative_to(tmp_path), (
        f"AUDIT_JSONL_PATH leaked outside tmp_path: {db_mod.AUDIT_JSONL_PATH}"
    )
    assert "audit.jsonl" in str(db_mod.AUDIT_JSONL_PATH)
    # also verify the constant imported at test_audit.py module load time
    # was the on-disk default (the monkeypatch happens per-test).
    assert AUDIT_JSONL_PATH.name == "audit.jsonl"
