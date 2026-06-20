"""Localhost-only bind verified.

The localhost bind is a hard invariant. TestClient bypasses TCP, so we
assert the module-level constant that scripts/start_orchestrator.sh + the
__main__ block use to configure uvicorn. The smoke step additionally runs
`lsof -nP -iTCP -sTCP:LISTEN` against the running process for end-to-end
evidence.
"""

from __future__ import annotations

from infra.app import UVICORN_HOST


def test_uvicorn_host_is_localhost():
    assert UVICORN_HOST == "127.0.0.1", (
        f"orchestration server must bind to localhost only. UVICORN_HOST is {UVICORN_HOST!r}."
    )
    assert UVICORN_HOST != "0.0.0.0"
