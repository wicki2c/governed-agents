"""Zero-LLM governance demo.

Reproduces the observable story of `scripts/demo.sh` — submit an external
proposal, see it blocked by the gate, approve it, watch it execute, read the
audit trail — but in pure deterministic Python via FastAPI's `TestClient`.
No real socket bind, no Anthropic key, no paid layer.

DB isolation: the live app's `init_db()` and module-level engine point at
the on-disk `infra/db.sqlite`. Running the demo against those would pollute
a real deployment's database. So we mirror the test harness
(`tests/conftest.py`): an in-memory SQLite engine (StaticPool, so the
TestClient's threads share one in-process DB), `get_session` overridden onto
it, the audit JSONL redirected to a temp file, and `init_db` no-op'd. When
the demo exits, the temp dir is removed and nothing on disk has changed.
"""

from __future__ import annotations

import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path


def _print_header(text: str) -> None:
    line = "─" * len(text)
    print(f"\n{text}\n{line}")


@contextmanager
def _isolated_client() -> Iterator[object]:
    """Yield a TestClient bound to a throwaway in-memory DB + temp audit log.

    Mirrors the conftest fixtures so the demo never touches infra/db.sqlite
    or infra/audit.jsonl.
    """
    from fastapi.testclient import TestClient
    from sqlalchemy.pool import StaticPool
    from sqlmodel import Session, SQLModel, create_engine

    import infra.app
    import infra.db
    import infra.watchdog
    from infra.db import KNOWN_AGENT_IDS, Agent, get_session

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)

    # Swap the module-level engines onto the throwaway DB.
    orig_db_engine = infra.db.engine
    orig_watchdog_engine = infra.watchdog.engine
    orig_db_audit = infra.db.AUDIT_JSONL_PATH
    orig_app_audit = infra.app.AUDIT_JSONL_PATH
    orig_init_db = infra.app.init_db

    with tempfile.TemporaryDirectory(prefix="governed-demo-") as tmp:
        audit_path = Path(tmp) / "audit.jsonl"
        audit_path.touch()

        infra.db.reset_engine_for_tests(engine)
        infra.watchdog.engine = engine
        infra.db.AUDIT_JSONL_PATH = audit_path
        infra.app.AUDIT_JSONL_PATH = audit_path
        infra.app.init_db = lambda: None  # tables already created above

        # Seed the known agent rows (the lifespan/init_db normally does this).
        with Session(engine) as s:
            for agent_id in KNOWN_AGENT_IDS:
                s.add(Agent(agent_id=agent_id))
            s.commit()

        def _override_session() -> Iterator[Session]:
            with Session(engine) as s:
                yield s

        infra.app.app.dependency_overrides[get_session] = _override_session
        try:
            with TestClient(infra.app.app) as client:
                yield client
        finally:
            infra.app.app.dependency_overrides.clear()
            infra.db.reset_engine_for_tests(orig_db_engine)
            infra.watchdog.engine = orig_watchdog_engine
            infra.db.AUDIT_JSONL_PATH = orig_db_audit
            infra.app.AUDIT_JSONL_PATH = orig_app_audit
            infra.app.init_db = orig_init_db


def run_demo() -> int:
    """Run the governance demo end-to-end. Returns 0 on success, 1 on failure."""
    agent = "demo-agent"
    now = datetime.now(UTC)
    expires = now + timedelta(minutes=30)

    print("governed-agents demo — the proposal-gate, end-to-end, no LLM.")
    print("(runs against an in-memory DB — your infra/db.sqlite is untouched)")

    with _isolated_client() as client:
        # ── 1. The gate refuses a self-approval ───────────────────────────
        _print_header("1. The gate cannot be self-approved")
        print(
            "  An agent submits an external 'publish_content' action and tries to\n"
            "  self-approve it (requires_approval=false). The policy lists\n"
            "  publish_content as never_auto_approve, so the server rejects it."
        )
        bad = client.post(
            "/proposals",
            json={
                "agent_id": agent,
                "wave": 0,
                "submitted_at": now.isoformat(),
                "action_type": "publish_content",
                "payload": {"title": "I approve myself", "url": "https://example.com/x"},
                "expected_outcome": "Publish without a human.",
                "reversible": True,
                "requires_approval": False,
                "expires_at": expires.isoformat(),
                "rationale": "Attempt to bypass the gate.",
            },
        )
        if bad.status_code != 400:
            print(f"  UNEXPECTED: self-approval was not blocked (HTTP {bad.status_code})")
            return 1
        detail = bad.json().get("detail", {})
        print(f"  → HTTP 400  {detail.get('error', 'self_approve_blocked')}")
        print(f"     {detail.get('message', '')}")
        print("     (recorded as audit_flag_self_approve_attempt)")

        # ── 2. A legitimate proposal lands PENDING ────────────────────────
        _print_header("2. A legitimate external proposal lands PENDING")
        print(
            "  The agent submits the same action honestly (requires_approval=true).\n"
            "  Because publish_content needs a human, it waits — PENDING."
        )
        resp = client.post(
            "/proposals",
            json={
                "agent_id": agent,
                "wave": 0,
                "submitted_at": now.isoformat(),
                "action_type": "publish_content",
                "payload": {
                    "title": "Hello from a governed agent",
                    "url": "https://example.com/post",
                },
                "expected_outcome": "Publish a post to the public site.",
                "reversible": False,
                "requires_approval": True,
                "expires_at": expires.isoformat(),
                "rationale": "Demo of the proposal-gate: this action needs a human.",
            },
        )
        if resp.status_code != 200:
            print(f"  UNEXPECTED: submit failed (HTTP {resp.status_code}): {resp.text}")
            return 1
        proposal_id = resp.json()["proposal_id"]
        status = resp.json()["status"]
        print(f"  → proposal {proposal_id}")
        print(f"     status: {status.upper()}  (the agent cannot proceed yet)")
        if status != "pending":
            print(f"  UNEXPECTED: expected PENDING, got {status}")
            return 1

        # ── 3. A human approves it ────────────────────────────────────────
        _print_header("3. A human approves it on the dashboard")
        print("  The operator clicks Approve. The orchestrator records the decision.")
        decided = client.post(
            f"/proposals/{proposal_id}/decision",
            json={"decision": "approve", "final": True, "note": "looks good — ship it"},
        )
        if decided.status_code != 200:
            print(f"  UNEXPECTED: decision failed (HTTP {decided.status_code}): {decided.text}")
            return 1
        print(f"  → status: {decided.json()['status'].upper()}")

        # ── 4. The agent executes and records it ──────────────────────────
        _print_header("4. The agent executes the approved action")
        print("  Now — and only now — the agent performs the action and audits it.")
        client.post(
            "/audit",
            json={
                "agent_id": agent,
                "event_type": "action_executed",
                "proposal_id": proposal_id,
                "payload": {"published": True},
            },
        )
        final = client.get(f"/proposals/{proposal_id}").json()
        print(f"  → proposal status: {final['status'].upper()}, action EXECUTED")

        # ── 5. The audit trail ────────────────────────────────────────────
        _print_header("5. The audit trail (oldest first)")
        audit = client.get(f"/audit?agent_id={agent}&limit=50").json()
        for row in reversed(audit):
            print(f"  {row['ts']}  {row['event_type']}")

    _print_header("done")
    print(
        "  The gate blocked a self-approval, held an external action for a human,\n"
        "  and recorded every step. That is the whole point.\n"
        "  Nothing was written to disk — this ran on a throwaway in-memory DB."
    )
    return 0
