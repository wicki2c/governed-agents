"""End-to-end test for the `publisher` example agent's decision hook.

Drives proposal -> approve -> hook side effect -> audit through the FastAPI
TestClient — the same loop a real deployment runs, minus the LLM. Proves the
second example agent's custom `publish_note` hook fires on approval and not
on rejection (the gate works).
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from agents.publisher import hooks as publisher_hooks
from infra import decision_hooks


@pytest.fixture(autouse=True)
def _hook_env(tmp_path, monkeypatch):
    """Point the hook's output at a tmp dir (no repo files written) and
    snapshot/restore the registry so the test leaves no residue. Importing
    `publisher_hooks` already registered `publish_note`; re-register if a
    prior teardown removed it."""
    monkeypatch.setattr(publisher_hooks, "OUTPUT_DIR", tmp_path)
    saved = dict(decision_hooks.HOOK_REGISTRY)
    saved_direct = dict(decision_hooks._DIRECT_CALLABLES)
    if publisher_hooks.ACTION_TYPE not in decision_hooks.HOOK_REGISTRY:
        decision_hooks.register(publisher_hooks.ACTION_TYPE, publisher_hooks.publish_note)
    yield
    decision_hooks.HOOK_REGISTRY.clear()
    decision_hooks.HOOK_REGISTRY.update(saved)
    decision_hooks._DIRECT_CALLABLES.clear()
    decision_hooks._DIRECT_CALLABLES.update(saved_direct)


def _iso(minutes: int = 0) -> str:
    return (datetime.now(UTC) + timedelta(minutes=minutes)).isoformat()


def _submit(client: TestClient, pid: str, slug: str) -> None:
    r = client.post(
        "/proposals",
        json={
            "proposal_id": pid,
            "agent_id": "publisher",
            "wave": 0,
            "submitted_at": _iso(),
            "action_type": "publish_note",
            "payload": {"slug": slug, "body": "# Hello\nFrom a governed agent."},
            "expected_outcome": "Publish a note once a human approves.",
            "reversible": True,
            "requires_approval": True,
            "expires_at": _iso(15),
            "rationale": "Demo of a custom post-approval decision hook.",
        },
    )
    assert r.status_code == 200
    # publish_note is not on the auto-approve list -> defaults to pending.
    assert r.json()["status"] == "pending"


def test_publish_note_hook_fires_on_approval(client: TestClient, tmp_path) -> None:
    pid = str(uuid.uuid4())
    _submit(client, pid, "hello-world")

    d = client.post(f"/proposals/{pid}/decision", json={"decision": "approve", "final": True})
    assert d.status_code == 200
    assert d.json()["status"] == "approved"

    # Side effect happened: the note was written by the hook.
    note = tmp_path / "hello-world.md"
    assert note.exists()
    assert "From a governed agent." in note.read_text(encoding="utf-8")

    # Audit chain recorded the gated decision; the hook did not error.
    types = [
        a["event_type"]
        for a in client.get("/audit", params={"agent_id": "publisher", "limit": 50}).json()
    ]
    assert "proposal_submitted" in types
    assert "proposal_decided" in types
    assert "audit_flag_decision_hook_error" not in types


def test_publish_note_not_published_on_rejection(client: TestClient, tmp_path) -> None:
    pid = str(uuid.uuid4())
    _submit(client, pid, "rejected-note")

    d = client.post(
        f"/proposals/{pid}/decision",
        json={"decision": "reject", "final": True, "note": "not now"},
    )
    assert d.status_code == 200
    assert d.json()["status"] == "rejected"

    # The gate held: a rejected proposal produces no side effect.
    assert not (tmp_path / "rejected-note.md").exists()


def test_publish_note_slug_cannot_escape_output_dir(tmp_path, monkeypatch) -> None:
    """A traversal-laden slug is sanitised to a safe stem inside OUTPUT_DIR."""
    monkeypatch.setattr(publisher_hooks, "OUTPUT_DIR", tmp_path)

    class _P:
        proposal_id = "p-x"
        payload = '{"slug": "../../etc/evil", "body": "x"}'

    result = publisher_hooks.publish_note(_P(), None)
    written = tmp_path / result["published"].split("/")[-1]
    assert written.parent == tmp_path  # never escaped the output dir
    assert ".." not in result["published"].split("/")[-1]
