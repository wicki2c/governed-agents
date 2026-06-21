"""Unit tests for the `governed-agents` CLI (``governed_agents/cli.py``).

The CLI is a thin dispatcher: every subcommand routes to an existing
``infra.*`` / ``agents.*`` entrypoint rather than reimplementing it. These
tests drive ``cli.main(argv)`` and patch at the boundary (``uvicorn.run``,
``infra.watchdog.loop``, ``agents.orchestrator.runner.main``, ``httpx.get``)
so nothing here binds a real socket, opens a network connection, or boots a
server. The ``demo`` and ``init`` paths run for real but against
throwaway / ``tmp_path`` stores, so the real ``infra/db.sqlite`` and
``infra/audit.jsonl`` are never touched.

The load-bearing test is :func:`test_serve_binds_localhost_only` — the
regression guard for the ADR-0004 localhost bind invariant.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from governed_agents import __version__, cli

# ---------------------------------------------------------------------------
# 1. Top-level dispatch / arg-parsing
# ---------------------------------------------------------------------------


def test_help_exits_zero(capsys):
    with pytest.raises(SystemExit) as exc:
        cli.main(["--help"])
    assert exc.value.code == 0
    out = capsys.readouterr().out
    assert "governed-agents" in out
    # All subcommands should be advertised in the help text.
    for cmd in ("init", "serve", "watchdog", "run", "demo", "status", "scoreboard"):
        assert cmd in out


def test_no_subcommand_exits_two(capsys):
    # A subparser is required; argparse exits 2 on a usage error.
    with pytest.raises(SystemExit) as exc:
        cli.main([])
    assert exc.value.code == 2


def test_unknown_subcommand_exits_two(capsys):
    with pytest.raises(SystemExit) as exc:
        cli.main(["does-not-exist"])
    assert exc.value.code == 2


def test_version_prints_package_version(capsys):
    with pytest.raises(SystemExit) as exc:
        cli.main(["--version"])
    assert exc.value.code == 0
    out = capsys.readouterr().out
    assert __version__ in out


@pytest.mark.parametrize(
    "command",
    ["init", "serve", "watchdog", "run", "demo", "status", "scoreboard"],
)
def test_all_seven_subcommands_registered(command):
    """Every documented subcommand parses and binds a handler.

    We parse a minimal valid argv for each (``run`` needs a positional
    ``agent``) and assert the parser attached a callable ``func`` — i.e. the
    subcommand is reachable — without invoking the handler.
    """
    parser = cli.build_parser()
    argv = [command]
    if command == "run":
        argv.append("noop")
    args = parser.parse_args(argv)
    assert callable(args.func)


def test_exactly_seven_subcommands_registered():
    parser = cli.build_parser()
    # The single subparsers action holds every registered subcommand.
    choices: dict = {}
    for action in parser._actions:
        if hasattr(action, "choices") and action.choices:
            # The subparsers action's choices map command -> subparser.
            if {"init", "serve"} <= set(action.choices):
                choices = action.choices
                break
    assert set(choices) == {
        "init",
        "serve",
        "watchdog",
        "run",
        "demo",
        "status",
        "scoreboard",
    }


# ---------------------------------------------------------------------------
# 2. serve — the localhost bind invariant (ADR-0004), the hard one.
# ---------------------------------------------------------------------------


def test_serve_binds_localhost_only(monkeypatch):
    """``serve`` must call uvicorn with the app import string and
    host == 127.0.0.1 (== infra.app.UVICORN_HOST), reload disabled.

    This is the regression guard for ADR-0004. Patch ``uvicorn.run`` so no
    socket is ever bound.
    """
    import uvicorn

    from infra import app

    calls: list[dict] = []

    def _fake_run(app_arg, **kwargs):
        calls.append({"app": app_arg, **kwargs})

    monkeypatch.setattr(uvicorn, "run", _fake_run)

    rc = cli.main(["serve"])
    assert rc == 0
    assert len(calls) == 1
    call = calls[0]
    assert call["app"] == "infra.app:app"
    assert call["host"] == "127.0.0.1"
    assert call["host"] == app.UVICORN_HOST
    assert call["reload"] is False
    # Default port falls through to infra.app.UVICORN_PORT.
    assert call["port"] == app.UVICORN_PORT


def test_serve_port_override_keeps_localhost(monkeypatch):
    """``--port`` overrides the port but the host stays 127.0.0.1."""
    import uvicorn

    calls: list[dict] = []
    monkeypatch.setattr(uvicorn, "run", lambda app_arg, **kw: calls.append({"app": app_arg, **kw}))

    rc = cli.main(["serve", "--port", "9999"])
    assert rc == 0
    assert calls[0]["port"] == 9999
    assert calls[0]["host"] == "127.0.0.1"


@pytest.mark.parametrize("host_flag", ["--host", "--bind", "--address"])
def test_serve_rejects_any_host_flag(host_flag):
    """There is deliberately NO way to pass a host/bind/address.

    argparse must reject every host-shaped flag with a usage error (exit 2).
    This nails the "no host flag by design" half of the invariant.
    """
    with pytest.raises(SystemExit) as exc:
        cli.main(["serve", host_flag, "0.0.0.0"])
    assert exc.value.code == 2


# ---------------------------------------------------------------------------
# 3. watchdog — delegates to infra.watchdog.loop
# ---------------------------------------------------------------------------


def test_watchdog_delegates_to_loop(monkeypatch):
    import infra.watchdog

    called = {"n": 0}
    monkeypatch.setattr(infra.watchdog, "loop", lambda: called.__setitem__("n", called["n"] + 1))

    rc = cli.main(["watchdog"])
    assert rc == 0
    assert called["n"] == 1


# ---------------------------------------------------------------------------
# 4. run — delegates to agents.orchestrator.runner.main with built argv
# ---------------------------------------------------------------------------


def test_run_builds_runner_argv(monkeypatch):
    from agents.orchestrator import runner

    captured: list[list[str]] = []

    def _fake_main(argv):
        captured.append(argv)
        return 0

    monkeypatch.setattr(runner, "main", _fake_main)

    rc = cli.main(["run", "noop", "smoke"])
    assert rc == 0
    assert captured == [["--agent", "noop", "--task", "smoke"]]


def test_run_default_task_is_smoke(monkeypatch):
    from agents.orchestrator import runner

    captured: list[list[str]] = []
    monkeypatch.setattr(runner, "main", lambda argv: captured.append(argv) or 0)

    rc = cli.main(["run", "noop"])
    assert rc == 0
    assert captured == [["--agent", "noop", "--task", "smoke"]]


def test_run_propagates_runner_exit_code(monkeypatch):
    from agents.orchestrator import runner

    monkeypatch.setattr(runner, "main", lambda argv: 7)
    assert cli.main(["run", "noop", "smoke"]) == 7


def test_run_requires_agent_positional():
    with pytest.raises(SystemExit) as exc:
        cli.main(["run"])
    assert exc.value.code == 2


# ---------------------------------------------------------------------------
# 5. status / scoreboard — clean failure when no server is running
# ---------------------------------------------------------------------------


def _patch_connect_error(monkeypatch):
    """Make httpx.get raise ConnectError as if no orchestrator is up."""
    import httpx

    def _boom(*_a, **_k):
        raise httpx.ConnectError("connection refused")

    monkeypatch.setattr(httpx, "get", _boom)


@pytest.mark.parametrize("command", ["status", "scoreboard"])
def test_status_scoreboard_clean_exit_when_no_server(command, monkeypatch, capsys):
    """No traceback when the orchestrator is down — exit 1 + readable hint."""
    _patch_connect_error(monkeypatch)

    rc = cli.main([command])
    assert rc == 1
    captured = capsys.readouterr()
    # The message goes to stderr and reads cleanly (no Python traceback).
    assert "could not connect" in captured.err
    assert "Traceback" not in captured.err
    assert "Traceback" not in captured.out


@pytest.mark.parametrize(
    ("command", "endpoint"),
    [("status", "/status"), ("scoreboard", "/scoreboard")],
)
def test_status_scoreboard_print_json_on_success(command, endpoint, monkeypatch, capsys):
    """A 200 response is pretty-printed as JSON and exits 0."""
    import httpx

    payload = {"ok": True, "agents_paused": ["noop"]}

    class _FakeResp:
        status_code = 200

        def json(self):
            return payload

    seen: dict = {}

    def _fake_get(url, **_kw):
        seen["url"] = url
        return _FakeResp()

    monkeypatch.setattr(httpx, "get", _fake_get)

    rc = cli.main([command])
    assert rc == 0
    out = capsys.readouterr().out
    assert json.loads(out) == payload
    assert seen["url"].endswith(endpoint)


def test_status_respects_url_override(monkeypatch, capsys):
    import httpx

    class _FakeResp:
        status_code = 200

        def json(self):
            return {}

    seen: dict = {}
    monkeypatch.setattr(
        httpx, "get", lambda url, **_kw: (seen.__setitem__("url", url), _FakeResp())[1]
    )

    rc = cli.main(["status", "--url", "http://127.0.0.1:1234/"])
    assert rc == 0
    # Trailing slash is stripped; endpoint appended.
    assert seen["url"] == "http://127.0.0.1:1234/status"


def test_status_non_200_exits_one(monkeypatch, capsys):
    import httpx

    class _FakeResp:
        status_code = 503
        text = "unavailable"

        def json(self):  # pragma: no cover - not reached on non-200
            return {}

    monkeypatch.setattr(httpx, "get", lambda url, **_kw: _FakeResp())

    rc = cli.main(["status"])
    assert rc == 1
    assert "503" in capsys.readouterr().err


# ---------------------------------------------------------------------------
# 6. demo — isolated store, gate behaviour exercised, no disk mutation
# ---------------------------------------------------------------------------


def test_demo_runs_and_exits_zero():
    """The demo runs end-to-end against an in-memory store and returns 0."""
    from governed_agents.demo import run_demo

    assert run_demo() == 0


def test_demo_does_not_touch_real_db_or_audit():
    """The demo must not create or mutate the on-disk infra stores.

    We snapshot the real files' existence + size before/after. The demo uses
    a throwaway in-memory engine and a temp audit log, so neither should
    change as a result of running it.
    """
    from governed_agents.demo import run_demo

    infra_dir = Path("infra")
    db_path = infra_dir / "db.sqlite"
    audit_path = infra_dir / "audit.jsonl"

    def _snapshot(p: Path):
        return (p.exists(), p.stat().st_size if p.exists() else None)

    before = (_snapshot(db_path), _snapshot(audit_path))
    assert run_demo() == 0
    after = (_snapshot(db_path), _snapshot(audit_path))
    assert before == after, (
        "demo mutated a real infra store: "
        f"db {before[0]} -> {after[0]}, audit {before[1]} -> {after[1]}"
    )


def test_demo_blocks_self_approval(capsys):
    """The gate behaviour is exercised: a self-approval is blocked.

    run_demo prints the HTTP 400 self_approve outcome and the audit trail.
    If the gate had let the self-approval through, run_demo returns 1 (its
    own UNEXPECTED guard), so a 0 return already proves the block — but we
    also assert the observable evidence in stdout.
    """
    from governed_agents.demo import run_demo

    rc = run_demo()
    out = capsys.readouterr().out
    assert rc == 0
    assert "cannot be self-approved" in out
    assert "HTTP 400" in out
    assert "self_approve" in out


def test_demo_via_cli_dispatch(monkeypatch):
    """`governed-agents demo` routes to run_demo and returns its code."""
    import governed_agents.demo as demo_mod

    monkeypatch.setattr(demo_mod, "run_demo", lambda: 0)
    assert cli.main(["demo"]) == 0


# ---------------------------------------------------------------------------
# 7. init — scaffolds into tmp_path; refuses to overwrite on a re-run
# ---------------------------------------------------------------------------


def test_init_scaffolds_into_empty_dir(tmp_path, capsys):
    target = tmp_path / "project"
    target.mkdir()

    rc = cli.main(["init", str(target)])
    assert rc == 0

    # Every bundled scaffold file should now exist under the target.
    scaffold_dir = cli.SCAFFOLD_DIR
    expected = sorted(p.relative_to(scaffold_dir) for p in scaffold_dir.rglob("*") if p.is_file())
    assert expected, "fixture sanity: bundled scaffold should not be empty"
    for rel in expected:
        assert (target / rel).is_file(), f"missing scaffolded file: {rel}"

    out = capsys.readouterr().out
    assert "scaffolded a new governed-agents project" in out


def test_init_refuses_to_overwrite_and_writes_nothing_new(tmp_path, capsys):
    target = tmp_path / "project"
    target.mkdir()

    # First run scaffolds successfully.
    assert cli.main(["init", str(target)]) == 0
    capsys.readouterr()  # drain

    # Capture the full file inventory + contents after the first run.
    def _inventory() -> dict[str, bytes]:
        return {
            str(p.relative_to(target)): p.read_bytes()
            for p in sorted(target.rglob("*"))
            if p.is_file()
        }

    before = _inventory()

    # Second run into the same dir must refuse and change nothing.
    rc = cli.main(["init", str(target)])
    assert rc == 1

    err = capsys.readouterr().err
    assert "refusing to overwrite" in err
    # It lists the collisions.
    assert any(name.split("/")[-1] in err for name in before)

    after = _inventory()
    assert after == before, "init clobbered or added files despite refusing"


def test_init_default_dir_is_current_dir():
    """The `dir` positional defaults to '.' (no overwrite path is taken)."""
    parser = cli.build_parser()
    args = parser.parse_args(["init"])
    assert args.dir == "."
