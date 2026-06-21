"""End-to-end test for the `notifier` example agent's decision hook.

Drives proposal -> approve -> hook side effect -> audit through the FastAPI
TestClient — the same loop a real deployment runs, minus the LLM. Proves the
third example agent's custom `notify_on_approval` hook fires on approval and
not on rejection (the gate works), and that an untrusted message cannot forge
extra log lines (log-injection guard).
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from agents.notifier import hooks as notifier_hooks
from infra import decision_hooks


@pytest.fixture(autouse=True)
def _hook_env(tmp_path, monkeypatch):
    """Point the hook's log at a tmp path (no repo files written) and
    snapshot/restore the registry so the test leaves no residue. Importing
    `notifier_hooks` already registered `notify_on_approval`; re-register if a
    prior teardown removed it. Restoring BOTH registry dicts keeps the
    publisher + notifier hooks from bleeding across the session."""
    monkeypatch.setattr(notifier_hooks, "NOTIFICATIONS_LOG", tmp_path / "notifications.log")
    saved = dict(decision_hooks.HOOK_REGISTRY)
    saved_direct = dict(decision_hooks._DIRECT_CALLABLES)
    if notifier_hooks.ACTION_TYPE not in decision_hooks.HOOK_REGISTRY:
        decision_hooks.register(notifier_hooks.ACTION_TYPE, notifier_hooks.notify_on_approval)
    yield
    decision_hooks.HOOK_REGISTRY.clear()
    decision_hooks.HOOK_REGISTRY.update(saved)
    decision_hooks._DIRECT_CALLABLES.clear()
    decision_hooks._DIRECT_CALLABLES.update(saved_direct)


def _iso(minutes: int = 0) -> str:
    return (datetime.now(UTC) + timedelta(minutes=minutes)).isoformat()


def _submit(client: TestClient, pid: str, message: str) -> None:
    r = client.post(
        "/proposals",
        json={
            "proposal_id": pid,
            "agent_id": "notifier",
            "wave": 0,
            "submitted_at": _iso(),
            "action_type": "notify_on_approval",
            "payload": {"message": message},
            "expected_outcome": "Emit a notification once a human approves.",
            "reversible": True,
            "requires_approval": True,
            "expires_at": _iso(15),
            "rationale": "Demo of a custom post-approval decision hook.",
        },
    )
    assert r.status_code == 200
    # notify_on_approval is not on the auto-approve list -> defaults to pending.
    assert r.json()["status"] == "pending"


def test_notify_fires_on_approval(client: TestClient, tmp_path) -> None:
    pid = str(uuid.uuid4())
    _submit(client, pid, "deploy finished")

    d = client.post(f"/proposals/{pid}/decision", json={"decision": "approve", "final": True})
    assert d.status_code == 200
    assert d.json()["status"] == "approved"

    # Side effect happened: the hook appended a line to the notifications log.
    log = tmp_path / "notifications.log"
    assert log.exists()
    text = log.read_text(encoding="utf-8")
    assert pid in text  # the proposal_id is in the line
    assert "notify_on_approval" in text  # the action_type marker substring
    assert "deploy finished" in text

    # Audit chain recorded the gated decision; the hook did not error.
    types = [
        a["event_type"]
        for a in client.get("/audit", params={"agent_id": "notifier", "limit": 50}).json()
    ]
    assert "proposal_submitted" in types
    assert "proposal_decided" in types
    assert "audit_flag_decision_hook_error" not in types


def test_notify_not_emitted_on_rejection(client: TestClient, tmp_path) -> None:
    pid = str(uuid.uuid4())
    _submit(client, pid, "should never be logged")

    d = client.post(
        f"/proposals/{pid}/decision",
        json={"decision": "reject", "final": True, "note": "not now"},
    )
    assert d.status_code == 200
    assert d.json()["status"] == "rejected"

    # The gate held: a rejected proposal produces no side effect, so the log
    # file is never even created.
    assert not (tmp_path / "notifications.log").exists()


def test_notify_message_cannot_inject_log_lines(tmp_path, monkeypatch) -> None:
    """A message with embedded newlines is sanitised to a single fragment, so
    a crafted payload cannot forge a second log line. Exercises the str-payload
    branch and the sanitize branch directly."""
    monkeypatch.setattr(notifier_hooks, "NOTIFICATIONS_LOG", tmp_path / "notifications.log")

    class _P:
        proposal_id = "p-inject"
        # payload is a JSON STRING (runtime shape) whose message tries to
        # forge a second line via embedded newlines + control chars.
        payload = '{"message": "ok\\nfake\\t2099-01-01\\tnotify_on_approval\\tp-evil\\tforged"}'

    result = notifier_hooks.notify_on_approval(_P(), None)
    log = tmp_path / "notifications.log"
    assert log.exists()
    written = log.read_text(encoding="utf-8")
    lines = written.splitlines()
    # Exactly ONE line was written — the injection was neutralised: the
    # embedded newline never produced a second log line.
    assert len(lines) == 1
    assert written.count("\n") == 1
    # The sanitizer stripped the embedded \n and \t, so the forged record
    # never became its own tab-delimited line; the real line's proposal_id
    # (column 3) is still our own id, not the forged "p-evil".
    assert lines[0].split("\t")[2] == "p-inject"
    assert "\t" not in lines[0].split("\t")[3]  # message column holds no tabs
    assert result["proposal_id"] == "p-inject"
    assert result["notified"] == str(tmp_path / "notifications.log")


def test_notify_default_message_when_absent(tmp_path, monkeypatch) -> None:
    """When the payload omits `message`, the hook falls back to its default
    string. Exercises the `.get(..., default)` branch."""
    monkeypatch.setattr(notifier_hooks, "NOTIFICATIONS_LOG", tmp_path / "notifications.log")

    class _P:
        proposal_id = "p-default"
        payload = "{}"  # JSON string with no "message" key

    result = notifier_hooks.notify_on_approval(_P(), None)
    log = tmp_path / "notifications.log"
    assert log.exists()
    written = log.read_text(encoding="utf-8")
    assert "A governed action was approved." in written
    assert "p-default" in written
    assert written.count("\n") == 1
    assert result["proposal_id"] == "p-default"


def test_notify_accepts_dict_payload(tmp_path, monkeypatch) -> None:
    """A proposal whose payload is already a dict (not a JSON string) skips
    the json.loads branch. Covers the `isinstance(payload, str)` false case."""
    monkeypatch.setattr(notifier_hooks, "NOTIFICATIONS_LOG", tmp_path / "notifications.log")

    class _P:
        proposal_id = "p-dict"

        def __init__(self) -> None:
            self.payload = {"message": "dict payload"}

    result = notifier_hooks.notify_on_approval(_P(), None)
    written = (tmp_path / "notifications.log").read_text(encoding="utf-8")
    assert "dict payload" in written
    assert result["proposal_id"] == "p-dict"


def test_notify_appends_not_overwrites(tmp_path, monkeypatch) -> None:
    """Two approvals append two lines — the side effect is append-only, the
    distinguishing shape from the publisher's overwrite-a-file hook."""
    monkeypatch.setattr(notifier_hooks, "NOTIFICATIONS_LOG", tmp_path / "notifications.log")

    class _P:
        proposal_id = "p-1"
        payload = '{"message": "first"}'

    class _P2:
        proposal_id = "p-2"
        payload = '{"message": "second"}'

    notifier_hooks.notify_on_approval(_P(), None)
    notifier_hooks.notify_on_approval(_P2(), None)
    written = (tmp_path / "notifications.log").read_text(encoding="utf-8")
    assert written.count("\n") == 2
    assert "first" in written
    assert "second" in written
