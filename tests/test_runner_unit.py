"""Unit tests for agents.orchestrator.runner.

We patch subprocess.Popen with a fake that emits scripted stream-json
events and patch httpx with an in-process recorder. No real `claude -p`
is invoked, no network calls are made.
"""

from __future__ import annotations

import json
import os
import signal
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest

from agents.orchestrator import runner

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def lane_dir(tmp_path: Path) -> Path:
    """Build a minimal noop lane on disk under tmp_path/agents/."""
    agents_root = tmp_path / "agents"
    noop = agents_root / "noop"
    (noop / "tasks").mkdir(parents=True)
    (noop / "workspace").mkdir()
    (noop / "tool_allowlist.toml").write_text(
        'allowed_tools = ["Read(agents/noop/**)", "Bash(curl http://127.0.0.1:8005/*)"]\n'
        "max_budget_usd = 0.05\n",
        encoding="utf-8",
    )
    (noop / "tasks" / "smoke.md").write_text("noop smoke task", encoding="utf-8")
    (noop / "CLAUDE.md").write_text("# noop\n", encoding="utf-8")
    return agents_root


@dataclass
class FakeProc:
    """Minimal stand-in for subprocess.Popen returned by our patch."""

    returncode: int = 0
    _lines: list[str] = field(default_factory=list)
    _idx: int = 0
    _killed: bool = False
    _signaled: bool = False
    stderr: Any = None

    class _Stdout:
        def __init__(self, parent: FakeProc):
            self._parent = parent

        def readline(self) -> str:
            if self._parent._idx >= len(self._parent._lines):
                return ""
            line = self._parent._lines[self._parent._idx]
            self._parent._idx += 1
            return line

    def __post_init__(self) -> None:
        self.stdout = self._Stdout(self)

    def poll(self) -> int | None:
        return None if (self._idx < len(self._lines) and not self._killed) else self.returncode

    def wait(self, timeout: float | None = None) -> int:
        del timeout
        return self.returncode

    def send_signal(self, sig: int) -> None:
        del sig
        self._signaled = True

    def kill(self) -> None:
        self._killed = True
        self.returncode = -signal.SIGKILL


class FakePopenFactory:
    """Records call args and returns a scripted FakeProc."""

    def __init__(self, lines: list[str], returncode: int = 0):
        self._lines = lines
        self._returncode = returncode
        self.calls: list[dict[str, Any]] = []
        self.proc: FakeProc | None = None

    def __call__(self, argv: list[str], **kwargs: Any) -> FakeProc:
        self.calls.append({"argv": argv, **kwargs})
        proc = FakeProc(returncode=self._returncode, _lines=list(self._lines))
        self.proc = proc
        return proc


class FakeClient:
    """In-process orchestrator client recorder."""

    def __init__(
        self,
        status: dict[str, Any] | None = None,
        status_raises: Exception | None = None,
    ):
        self._status = status or {"agent_id": "noop", "status": "ok", "paused_reason": None}
        self._status_raises = status_raises
        self.charges: list[dict[str, Any]] = []
        self.audits: list[dict[str, Any]] = []

    def status_me(self, agent_id: str) -> dict[str, Any]:
        if self._status_raises is not None:
            raise self._status_raises
        return self._status

    def charge_budget(
        self,
        agent_id: str,
        *,
        tokens: int,
        tool: str,
        dollars: float | None = None,
        task_id: str | None = None,
    ) -> dict[str, Any]:
        self.charges.append(
            {
                "agent_id": agent_id,
                "tokens": tokens,
                "tool": tool,
                "dollars": dollars,
                "task_id": task_id,
            }
        )
        return {"agent_id": agent_id, "tokens_consumed": tokens, "dollars_consumed": dollars or 0.0}

    def post_audit(
        self,
        *,
        event_type: str,
        agent_id: str | None = None,
        proposal_id: str | None = None,
        task_id: str | None = None,
        payload: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        self.audits.append(
            {
                "event_type": event_type,
                "agent_id": agent_id,
                "proposal_id": proposal_id,
                "task_id": task_id,
                "payload": payload or {},
            }
        )
        return {"audit_id": len(self.audits)}


def _system_init(model: str = "claude-opus-4-7") -> str:
    return (
        json.dumps(
            {
                "type": "system",
                "subtype": "init",
                "session_id": "deadbeef-0000-4000-8000-000000000000",
                "model": model,
                "cwd": "/tmp",
            }
        )
        + "\n"
    )


def _result_event(
    *,
    input_tokens: int = 1000,
    output_tokens: int = 500,
    cost_usd: float = 0.018,
    is_error: bool = False,
    subtype: str = "success",
    model: str = "claude-opus-4-7",
) -> str:
    return (
        json.dumps(
            {
                "type": "result",
                "subtype": subtype,
                "is_error": is_error,
                "duration_ms": 8421,
                "num_turns": 3,
                "total_cost_usd": cost_usd,
                "modelUsage": {
                    model: {
                        "inputTokens": input_tokens,
                        "outputTokens": output_tokens,
                        "cacheReadInputTokens": 0,
                        "cacheCreationInputTokens": 0,
                        "costUSD": cost_usd,
                    }
                },
                "permission_denials": [],
                "terminal_reason": "stop",
                "errors": [],
            }
        )
        + "\n"
    )


# ---------------------------------------------------------------------------
# compose_argv (pure)
# ---------------------------------------------------------------------------


def test_compose_argv_pins_v2_1_143_flag_contract(lane_dir: Path) -> None:
    lane = runner.LaneConfig.load("noop", agents_dir=lane_dir)
    argv = runner.compose_argv(
        lane,
        prompt_text="hello",
        claude_bin="/fake/claude",
        model="claude-opus-4-7",
        session_id="11111111-2222-3333-4444-555555555555",
    )
    assert argv[0] == "/fake/claude"
    assert argv[1] == "-p"
    assert "--output-format" in argv
    assert argv[argv.index("--output-format") + 1] == "stream-json"
    # stream-json requires --verbose to emit anything (v2.1.143)
    assert "--verbose" in argv
    # --max-turns was removed in 2.1.143; we must use --max-budget-usd
    assert "--max-turns" not in argv
    assert "--max-budget-usd" in argv
    # Never pass dangerously-skip-permissions
    assert "--dangerously-skip-permissions" not in argv
    # Allowed-tools is comma-separated tool patterns (code-reviewer FLAG
    # follow-up: tool patterns themselves contain spaces, so comma is the
    # only unambiguous separator).
    at_idx = argv.index("--allowed-tools")
    allowed_blob = argv[at_idx + 1]
    assert "Read(agents/noop/**)" in allowed_blob
    assert "Bash(curl http://127.0.0.1:8005/*)" in allowed_blob
    # Verify the separator is a comma, not a space
    assert "Read(agents/noop/**),Bash(curl http://127.0.0.1:8005/*)" in allowed_blob
    # Prompt is the trailing positional
    assert argv[-1] == "hello"
    # Session id is what we passed
    assert "--session-id" in argv
    assert argv[argv.index("--session-id") + 1] == "11111111-2222-3333-4444-555555555555"
    # No-session-persistence is set so we don't litter ~/.claude/projects/
    assert "--no-session-persistence" in argv


# ---------------------------------------------------------------------------
# parse_stream_events + summarise_events (pure)
# ---------------------------------------------------------------------------


def test_summarise_events_extracts_usage_from_result() -> None:
    events = runner.parse_stream_events(
        [
            _system_init(),
            _result_event(input_tokens=2000, output_tokens=300, cost_usd=0.025),
        ]
    )
    summary = runner.summarise_events(events)
    assert summary.result_seen is True
    assert summary.is_error is False
    assert summary.tokens == 2300  # 2000 input + 300 output
    assert summary.dollars == pytest.approx(0.025)
    assert summary.session_id == "deadbeef-0000-4000-8000-000000000000"


def test_summarise_events_handles_missing_result() -> None:
    events = runner.parse_stream_events([_system_init()])
    summary = runner.summarise_events(events)
    assert summary.result_seen is False
    assert summary.tokens == 0
    assert summary.dollars == 0.0


def test_parse_stream_events_skips_garbage_lines() -> None:
    lines = [
        _system_init(),
        "not-json\n",
        "\n",
        _result_event(),
    ]
    events = list(runner.parse_stream_events(lines))
    assert len(events) == 2
    assert events[0]["type"] == "system"
    assert events[1]["type"] == "result"


def test_summarise_events_error_subtype_sets_is_error() -> None:
    events = runner.parse_stream_events(
        [_result_event(is_error=True, subtype="error_max_budget_usd", cost_usd=0.0)]
    )
    summary = runner.summarise_events(events)
    assert summary.is_error is True
    assert summary.subtype == "error_max_budget_usd"


# ---------------------------------------------------------------------------
# run_lane: happy path + failure modes
# ---------------------------------------------------------------------------


def test_run_lane_happy_path_charges_and_audits(lane_dir: Path) -> None:
    client = FakeClient()
    popen = FakePopenFactory([_system_init(), _result_event(input_tokens=1000, output_tokens=200)])
    outcome = runner.run_lane(
        "noop",
        client=client,
        agents_dir=lane_dir,
        popen=popen,
    )
    assert outcome.exit_code == 0
    assert outcome.reason == "success"
    # Exactly one charge with the observed token total
    assert len(client.charges) == 1
    assert client.charges[0]["tokens"] == 1200
    assert client.charges[0]["tool"] == "claude_call"
    # Exactly one terminal audit, of the right type
    types = [a["event_type"] for a in client.audits]
    assert "agent_run_complete" in types
    assert "agent_run_failed" not in types


def test_run_lane_paused_agent_short_circuits(lane_dir: Path) -> None:
    client = FakeClient(
        status={"agent_id": "noop", "status": "paused", "paused_reason": "stuck_task"}
    )
    popen = FakePopenFactory([_system_init(), _result_event()])
    outcome = runner.run_lane(
        "noop",
        client=client,
        agents_dir=lane_dir,
        popen=popen,
    )
    # Exit 0 — paused is not a failure, it's a deferred run
    assert outcome.exit_code == 0
    assert outcome.reason == "paused"
    # No subprocess spawned
    assert popen.calls == []
    # No budget charge
    assert client.charges == []
    # Audit: agent_run_skipped
    assert client.audits == [
        {
            "event_type": "agent_run_skipped",
            "agent_id": "noop",
            "proposal_id": None,
            "task_id": None,
            "payload": {"reason": "paused", "paused_reason": "stuck_task"},
        }
    ]


def test_run_lane_missing_result_event_emits_unknown_usage_audit(lane_dir: Path) -> None:
    client = FakeClient()
    # No result event — only system init
    popen = FakePopenFactory([_system_init()])
    outcome = runner.run_lane(
        "noop",
        client=client,
        agents_dir=lane_dir,
        popen=popen,
    )
    assert outcome.exit_code == 1
    assert outcome.reason == "no_result_event"
    # Both the unknown-usage audit AND the agent_run_failed audit must fire
    types = [a["event_type"] for a in client.audits]
    assert "budget_charge_unknown_usage" in types
    assert "agent_run_failed" in types
    # Charge is zero
    assert client.charges[0]["tokens"] == 0


def test_run_lane_is_error_true_is_a_failure(lane_dir: Path) -> None:
    client = FakeClient()
    popen = FakePopenFactory(
        [_system_init(), _result_event(is_error=True, subtype="error_max_budget_usd", cost_usd=0.0)]
    )
    outcome = runner.run_lane("noop", client=client, agents_dir=lane_dir, popen=popen)
    assert outcome.exit_code == 1
    assert outcome.reason.startswith("claude_error")
    types = [a["event_type"] for a in client.audits]
    assert "agent_run_failed" in types


def test_run_lane_unknown_agent_id_raises_for_main_to_handle(lane_dir: Path) -> None:
    client = FakeClient()
    popen = FakePopenFactory([_system_init(), _result_event()])
    with pytest.raises(ValueError):
        runner.run_lane(
            "definitely-not-a-real-lane",
            client=client,
            agents_dir=lane_dir,
            popen=popen,
        )


def test_main_unknown_agent_returns_exit_code_2(
    lane_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(runner, "AGENTS_DIR", lane_dir)
    rc = runner.main(["--agent", "definitely-not-a-real-lane"])
    assert rc == 2


def test_main_missing_allowlist_returns_exit_code_2(
    lane_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Remove the allowlist file
    (lane_dir / "noop" / "tool_allowlist.toml").unlink()
    monkeypatch.setattr(runner, "AGENTS_DIR", lane_dir)
    rc = runner.main(["--agent", "noop"])
    assert rc == 2


def test_run_lane_orchestrator_unreachable(lane_dir: Path) -> None:
    import httpx as _httpx

    client = FakeClient(status_raises=_httpx.ConnectError("connection refused"))
    popen = FakePopenFactory([_system_init(), _result_event()])
    outcome = runner.run_lane(
        "noop",
        client=client,
        agents_dir=lane_dir,
        popen=popen,
    )
    assert outcome.exit_code == 1
    assert "orchestrator_unreachable" in outcome.reason
    # No subprocess spawned
    assert popen.calls == []
    # No charge or audit (we can't reach the orchestrator)
    assert client.charges == []
    assert client.audits == []


def test_compose_argv_omits_dangerously_skip_permissions(lane_dir: Path) -> None:
    """Belt-and-braces: ensure no construction path can introduce
    --dangerously-skip-permissions. This is a non-negotiable."""
    lane = runner.LaneConfig.load("noop", agents_dir=lane_dir)
    for prompt in ["hi", "", "very long " * 500]:
        argv = runner.compose_argv(lane, prompt_text=prompt)
        assert "--dangerously-skip-permissions" not in argv
        assert "--permission-mode" in argv
        # Permission mode is "default" — never "bypassPermissions"
        assert argv[argv.index("--permission-mode") + 1] == "default"


def test_run_lane_passes_env_to_subprocess(lane_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Child env is the parent env (copy). This guarantees
    CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS=1 is propagated if set on the
    parent shell."""
    monkeypatch.setenv("CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS", "1")
    client = FakeClient()
    popen = FakePopenFactory([_system_init(), _result_event()])
    runner.run_lane("noop", client=client, agents_dir=lane_dir, popen=popen)
    assert popen.calls[0]["env"]["CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS"] == "1"


def test_run_lane_cwd_is_lane_dir(lane_dir: Path) -> None:
    """Confine the child's filesystem view to the lane directory."""
    client = FakeClient()
    popen = FakePopenFactory([_system_init(), _result_event()])
    runner.run_lane("noop", client=client, agents_dir=lane_dir, popen=popen)
    assert popen.calls[0]["cwd"] == str(lane_dir / "noop")
    # Importantly NOT the repo root or "/"
    assert popen.calls[0]["cwd"] != os.getcwd()


# ---------------------------------------------------------------------------
# --task flag: LaneConfig.load picks tasks/<task>.md
# ---------------------------------------------------------------------------


def test_lane_config_load_default_task_is_smoke(lane_dir: Path) -> None:
    """Default behaviour preserved: noop's prompt path resolves to tasks/smoke.md."""
    lane = runner.LaneConfig.load("noop", agents_dir=lane_dir)
    assert lane.prompt_path == lane_dir / "noop" / "tasks" / "smoke.md"


def test_lane_config_load_named_task_resolves(lane_dir: Path) -> None:
    """--task <name> resolves to tasks/<name>.md so a single agent can
    carry multiple task prompts."""
    (lane_dir / "noop" / "tasks" / "niche-select.md").write_text("named task", encoding="utf-8")
    lane = runner.LaneConfig.load("noop", agents_dir=lane_dir, task="niche-select")
    assert lane.prompt_path == lane_dir / "noop" / "tasks" / "niche-select.md"
    assert lane.prompt_path.read_text(encoding="utf-8") == "named task"


def test_lane_config_load_named_task_missing_file_raises(lane_dir: Path) -> None:
    """Missing tasks/<task>.md must raise FileNotFoundError (caller maps to exit 2)."""
    with pytest.raises(FileNotFoundError):
        runner.LaneConfig.load("noop", agents_dir=lane_dir, task="does-not-exist")


def test_main_task_flag_passed_through(lane_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """End-to-end via main(): --task must reach LaneConfig.load."""
    (lane_dir / "noop" / "tasks" / "alt-task.md").write_text("alt", encoding="utf-8")
    captured: dict[str, Any] = {}

    def fake_run_lane(agent_id: str, **kwargs: Any) -> runner.RunOutcome:
        captured["agent_id"] = agent_id
        captured["task"] = kwargs.get("task")
        return runner.RunOutcome(exit_code=0, summary=runner.StreamSummary(), reason="success")

    monkeypatch.setattr(runner, "AGENTS_DIR", lane_dir)
    monkeypatch.setattr(runner, "run_lane", fake_run_lane)
    rc = runner.main(["--agent", "noop", "--task", "alt-task"])
    assert rc == 0
    assert captured == {"agent_id": "noop", "task": "alt-task"}


def test_run_lane_passes_task_to_lane_config(lane_dir: Path) -> None:
    """End-to-end: a named task threads through run_lane → LaneConfig.load
    → prompt file read, and the prompt body is what reaches compose_argv."""
    (lane_dir / "noop" / "tasks" / "alt-task.md").write_text("alt task body", encoding="utf-8")
    client = FakeClient()
    popen = FakePopenFactory([_system_init(), _result_event()])
    outcome = runner.run_lane(
        "noop",
        task="alt-task",
        client=client,
        agents_dir=lane_dir,
        popen=popen,
    )
    assert outcome.exit_code == 0
    # The prompt text is the final positional argv element.
    argv = popen.calls[0]["argv"]
    assert argv[-1] == "alt task body"
