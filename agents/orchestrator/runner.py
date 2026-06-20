"""Runtime-agent runner. Wraps a single `claude -p` invocation for one lane.

Entry: `python -m agents.orchestrator.runner --agent <lane>`.

The runner is the trust boundary between a runtime agent and the
orchestration server. It:

1. Validates the lane id against `infra.db.KNOWN_AGENT_IDS`.
2. Refuses to start if the lane is `paused` (per `GET /status/me`).
3. Loads the agent's tool allowlist from `agents/<agent>/tool_allowlist.toml`
   (default-deny; explicit per-agent).
4. Composes the `claude -p` argv with `--output-format stream-json
   --verbose --max-budget-usd ... --no-session-persistence`.
5. Spawns the child via `subprocess.Popen` and stream-parses stdout
   NDJSON line by line.
6. Enforces a wall-clock deadline (SIGTERM, then SIGKILL +5s).
7. Posts the budget charge with tokens/dollars extracted from the
   terminal `result.usage` event — NOT from agent self-reports.
8. Writes `agent_run_complete` or `agent_run_failed` to the audit log.

Failure modes:
- Missing terminal `result` event → charge 0, write
  `budget_charge_unknown_usage` audit, exit 1.
- `result.is_error == True` → charge whatever usage was present, write
  `agent_run_failed`, exit 1.
- Wall-clock deadline → SIGTERM the child, write `agent_run_failed`
  with reason `wallclock_exceeded`, exit 1.
- Lane paused → write `agent_run_skipped`, exit 0 (this is not an
  error; the orchestrator should keep cycling other lanes).
- Unknown lane id → exit 2 (configuration error, not a run failure).
"""

from __future__ import annotations

import argparse
import json
import os
import signal
import subprocess
import sys
import time
import tomllib
import uuid
from collections.abc import Iterable, Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx
import structlog

from infra.db import KNOWN_AGENT_IDS

log = structlog.get_logger(__name__)

# ---------------------------------------------------------------------------
# Configuration — all overridable via env so the test suite can shrink them.
# ---------------------------------------------------------------------------

ORCHESTRATOR_BASE_URL = os.environ.get("ORCHESTRATOR_BASE_URL", "http://127.0.0.1:8005")
CLAUDE_BIN = os.environ.get("CLAUDE_BIN", "claude")
CLAUDE_MODEL = os.environ.get("CLAUDE_MODEL", "claude-opus-4-7")
PER_TASK_WALLCLOCK_SECONDS = int(os.environ.get("PER_TASK_WALLCLOCK_SECONDS", "1800"))
MAX_BUDGET_USD = float(os.environ.get("MAX_BUDGET_USD", "0.10"))
SIGKILL_GRACE_SECONDS = float(os.environ.get("SIGKILL_GRACE_SECONDS", "5.0"))

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
AGENTS_DIR = REPO_ROOT / "agents"


# ---------------------------------------------------------------------------
# Lane configuration
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class LaneConfig:
    """Resolved per-lane runtime config. Constructed from
    `agents/<lane>/tool_allowlist.toml` (default-deny; missing file is an
    error)."""

    agent_id: str
    allowed_tools: list[str]
    cwd: Path
    prompt_path: Path
    max_budget_usd: float = MAX_BUDGET_USD

    @classmethod
    def load(
        cls,
        agent_id: str,
        *,
        agents_dir: Path = AGENTS_DIR,
        task: str = "smoke",
    ) -> LaneConfig:
        if agent_id not in KNOWN_AGENT_IDS:
            raise ValueError(
                f"unknown agent_id {agent_id!r}; must be one of {sorted(KNOWN_AGENT_IDS)}"
            )
        lane_dir = agents_dir / agent_id
        allowlist_path = lane_dir / "tool_allowlist.toml"
        if not allowlist_path.is_file():
            raise FileNotFoundError(
                f"lane {agent_id!r} is missing tool_allowlist.toml at {allowlist_path} "
                "(default-deny: a per-agent tool allowlist is required)"
            )
        data = tomllib.loads(allowlist_path.read_text(encoding="utf-8"))
        tools = data.get("allowed_tools", [])
        if not isinstance(tools, list) or not all(isinstance(t, str) for t in tools):
            raise ValueError(f"{allowlist_path}: [allowed_tools] must be a list of strings")
        prompt_path = lane_dir / "tasks" / f"{task}.md"
        if not prompt_path.is_file():
            raise FileNotFoundError(
                f"lane {agent_id!r} is missing task prompt at {prompt_path} (task={task!r})"
            )
        max_budget = float(data.get("max_budget_usd", MAX_BUDGET_USD))
        return cls(
            agent_id=agent_id,
            allowed_tools=list(tools),
            cwd=lane_dir,
            prompt_path=prompt_path,
            max_budget_usd=max_budget,
        )


# ---------------------------------------------------------------------------
# argv composition (pure; unit-testable)
# ---------------------------------------------------------------------------


def compose_argv(
    lane: LaneConfig,
    *,
    prompt_text: str,
    claude_bin: str = CLAUDE_BIN,
    model: str = CLAUDE_MODEL,
    session_id: str | None = None,
) -> list[str]:
    """Build the `claude -p` command line for this lane.

    Notes pinned to the current claude -p flag contract:
    - prompt is the trailing positional, not `-p "<x>"`.
    - `--output-format stream-json` requires `--verbose`.
    - `--max-budget-usd` replaces the removed `--max-turns`.
    - `--allowed-tools` is comma-separated (code-reviewer FLAG follow-up:
      patterns like `"Bash(curl http://127.0.0.1:8005/*)"` themselves
      contain spaces, so space-joining was ambiguous to CC's flag parser).
    """
    sid = session_id or str(uuid.uuid4())
    return [
        claude_bin,
        "-p",
        "--model",
        model,
        "--output-format",
        "stream-json",
        "--verbose",
        "--max-budget-usd",
        f"{lane.max_budget_usd:.4f}",
        "--session-id",
        sid,
        "--no-session-persistence",
        "--permission-mode",
        "default",
        "--allowed-tools",
        ",".join(lane.allowed_tools),
        "--setting-sources",
        "project",
        "--strict-mcp-config",
        prompt_text,
    ]


# ---------------------------------------------------------------------------
# Stream-json parsing (pure; unit-testable)
# ---------------------------------------------------------------------------


@dataclass
class StreamSummary:
    """What we extract from a `claude -p --output-format stream-json` run."""

    session_id: str | None = None
    model: str | None = None
    tokens: int = 0
    dollars: float = 0.0
    result_seen: bool = False
    is_error: bool = False
    subtype: str | None = None
    terminal_reason: str | None = None
    permission_denials: list[dict[str, Any]] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


def parse_stream_events(lines: Iterable[str]) -> Iterator[dict[str, Any]]:
    """Yield parsed JSON objects from NDJSON stdout. Skips blanks and
    lines that fail to parse (logged at debug)."""
    for raw in lines:
        line = raw.strip()
        if not line:
            continue
        try:
            yield json.loads(line)
        except json.JSONDecodeError:
            log.debug("stream_json_skip_unparseable_line", line=line[:200])
            continue


def summarise_events(events: Iterable[dict[str, Any]]) -> StreamSummary:
    """Fold a sequence of stream-json events into a StreamSummary."""
    s = StreamSummary()
    for ev in events:
        etype = ev.get("type")
        if etype == "system" and ev.get("subtype") == "init":
            s.session_id = ev.get("session_id")
            s.model = ev.get("model")
            continue
        if etype == "result":
            s.result_seen = True
            s.is_error = bool(ev.get("is_error", False))
            s.subtype = ev.get("subtype")
            s.terminal_reason = ev.get("terminal_reason")
            s.permission_denials = list(ev.get("permission_denials", []))
            s.errors = list(ev.get("errors", []))
            # Tokens & dollars: prefer modelUsage (per-model breakdown),
            # fall back to total_cost_usd. Sum input+output+cache for
            # tokens; sum costUSD across models for dollars.
            mu = ev.get("modelUsage") or {}
            t_total = 0
            d_total = 0.0
            for _model, usage in mu.items():
                t_total += int(usage.get("inputTokens", 0) or 0)
                t_total += int(usage.get("outputTokens", 0) or 0)
                t_total += int(usage.get("cacheReadInputTokens", 0) or 0)
                t_total += int(usage.get("cacheCreationInputTokens", 0) or 0)
                d_total += float(usage.get("costUSD", 0.0) or 0.0)
            if mu:
                s.tokens = t_total
                s.dollars = d_total
            else:
                # total_cost_usd is the fallback if per-model breakdown is
                # absent; we cannot derive tokens from dollars without the
                # rate, so tokens stays 0 and we'll write a
                # budget_charge_unknown_usage audit later.
                s.dollars = float(ev.get("total_cost_usd", 0.0) or 0.0)
                s.tokens = 0
            continue
    return s


# ---------------------------------------------------------------------------
# Orchestrator HTTP client (thin)
# ---------------------------------------------------------------------------


class OrchestratorClient:
    def __init__(self, base_url: str = ORCHESTRATOR_BASE_URL, *, timeout: float = 10.0):
        self._base = base_url.rstrip("/")
        self._timeout = timeout

    def status_me(self, agent_id: str) -> dict[str, Any]:
        r = httpx.get(
            f"{self._base}/status/me",
            params={"agent_id": agent_id},
            timeout=self._timeout,
        )
        r.raise_for_status()
        return r.json()

    def charge_budget(
        self,
        agent_id: str,
        *,
        tokens: int,
        tool: str,
        dollars: float | None = None,
        task_id: str | None = None,
    ) -> dict[str, Any]:
        body: dict[str, Any] = {"tokens": tokens, "tool": tool}
        if dollars is not None:
            body["dollars"] = dollars
        if task_id is not None:
            body["task_id"] = task_id
        r = httpx.post(
            f"{self._base}/budget/{agent_id}/charge",
            json=body,
            timeout=self._timeout,
        )
        r.raise_for_status()
        return r.json()

    def post_audit(
        self,
        *,
        event_type: str,
        agent_id: str | None = None,
        proposal_id: str | None = None,
        task_id: str | None = None,
        payload: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        body: dict[str, Any] = {"event_type": event_type, "payload": payload or {}}
        if agent_id is not None:
            body["agent_id"] = agent_id
        if proposal_id is not None:
            body["proposal_id"] = proposal_id
        if task_id is not None:
            body["task_id"] = task_id
        r = httpx.post(f"{self._base}/audit", json=body, timeout=self._timeout)
        r.raise_for_status()
        return r.json()


# ---------------------------------------------------------------------------
# Run loop
# ---------------------------------------------------------------------------


@dataclass
class RunOutcome:
    exit_code: int
    summary: StreamSummary
    reason: str


def _read_stream_lines(stdout: Any, *, deadline: float) -> Iterator[str]:
    """Yield lines from stdout until EOF or wallclock deadline."""
    while True:
        if time.monotonic() > deadline:
            return
        line = stdout.readline()
        if not line:
            return
        yield line if isinstance(line, str) else line.decode("utf-8", errors="replace")


def _terminate(proc: subprocess.Popen[Any]) -> None:
    """SIGTERM, then SIGKILL after SIGKILL_GRACE_SECONDS."""
    if proc.poll() is not None:
        return
    try:
        proc.send_signal(signal.SIGTERM)
    except (ProcessLookupError, OSError):
        return
    try:
        proc.wait(timeout=SIGKILL_GRACE_SECONDS)
    except subprocess.TimeoutExpired:
        try:
            proc.kill()
        except (ProcessLookupError, OSError):
            pass


def run_lane(
    agent_id: str,
    *,
    task: str = "smoke",
    client: OrchestratorClient | None = None,
    agents_dir: Path | None = None,
    claude_bin: str | None = None,
    model: str | None = None,
    wallclock_seconds: int | None = None,
    popen: Any = subprocess.Popen,
) -> RunOutcome:
    """Run one task for `agent_id`. Returns the outcome (does not call sys.exit).

    Injection points (`client`, `popen`) exist for the unit tests; in prod
    they default to the real httpx client and real subprocess.Popen. The
    `agents_dir` / `claude_bin` / `model` / `wallclock_seconds` defaults
    are resolved at call time (not function-def time) so tests can
    monkeypatch the module-level constants.
    """
    client = client or OrchestratorClient()
    agents_dir = agents_dir or AGENTS_DIR
    claude_bin = claude_bin or CLAUDE_BIN
    model = model or CLAUDE_MODEL
    wallclock_seconds = (
        wallclock_seconds if wallclock_seconds is not None else PER_TASK_WALLCLOCK_SECONDS
    )

    # 1. Lane config (raises on unknown id / missing files — caller maps to exit 2).
    lane = LaneConfig.load(agent_id, agents_dir=agents_dir, task=task)

    # 2. Pause check.
    try:
        status = client.status_me(agent_id)
    except httpx.HTTPError as exc:
        log.error("status_me_failed", agent_id=agent_id, error=str(exc))
        return RunOutcome(
            exit_code=1,
            summary=StreamSummary(),
            reason=f"orchestrator_unreachable: {exc}",
        )
    if status.get("status") == "paused":
        log.info("agent_paused_skip_run", agent_id=agent_id, reason=status.get("paused_reason"))
        client.post_audit(
            event_type="agent_run_skipped",
            agent_id=agent_id,
            payload={"reason": "paused", "paused_reason": status.get("paused_reason")},
        )
        return RunOutcome(exit_code=0, summary=StreamSummary(), reason="paused")

    # 3. Compose argv + spawn.
    prompt_text = lane.prompt_path.read_text(encoding="utf-8")
    session_id = str(uuid.uuid4())
    argv = compose_argv(
        lane,
        prompt_text=prompt_text,
        claude_bin=claude_bin,
        model=model,
        session_id=session_id,
    )
    log.info(
        "claude_invoke",
        agent_id=agent_id,
        session_id=session_id,
        argv_head=argv[:6],
        allowed_tools=lane.allowed_tools,
        max_budget_usd=lane.max_budget_usd,
    )
    deadline = time.monotonic() + wallclock_seconds
    proc = popen(
        argv,
        cwd=str(lane.cwd),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=os.environ.copy(),
    )

    # 4. Stream-parse stdout.
    events = parse_stream_events(_read_stream_lines(proc.stdout, deadline=deadline))
    summary = summarise_events(events)
    wallclock_exceeded = time.monotonic() > deadline
    if wallclock_exceeded:
        _terminate(proc)
    # Bounded wait — code-reviewer FLAG: an unconditional proc.wait() can
    # hang if the child closes stdout but doesn't exit. After EOF +
    # optional _terminate this should return promptly; if not, kill.
    try:
        proc.wait(timeout=SIGKILL_GRACE_SECONDS + 5.0)
    except subprocess.TimeoutExpired:
        try:
            proc.kill()
        except (ProcessLookupError, OSError):
            pass
        proc.wait()

    # 5. Charge budget (the parent runner does this, not the agent).
    # Code-reviewer FLAG follow-up: post-run network calls are wrapped so
    # a late orchestrator outage doesn't crash the runner before we can
    # log it. We log the failure and still return a non-zero outcome.
    post_run_errors: list[str] = []
    try:
        if not summary.result_seen or summary.tokens == 0:
            client.post_audit(
                event_type="budget_charge_unknown_usage",
                agent_id=agent_id,
                payload={
                    "session_id": summary.session_id or session_id,
                    "result_seen": summary.result_seen,
                    "subtype": summary.subtype,
                },
            )
    except (httpx.HTTPError, OSError) as exc:
        post_run_errors.append(f"unknown_usage_audit_failed:{exc}")
        log.error("post_run_audit_failed", phase="unknown_usage", error=str(exc))
    try:
        client.charge_budget(
            agent_id,
            tokens=summary.tokens,
            tool="claude_call",
            dollars=summary.dollars if summary.dollars > 0 else None,
            task_id=session_id,
        )
    except (httpx.HTTPError, OSError) as exc:
        post_run_errors.append(f"charge_failed:{exc}")
        log.error("post_run_charge_failed", error=str(exc))

    # 6. Final audit + outcome.
    failed = (
        wallclock_exceeded
        or not summary.result_seen
        or summary.is_error
        or proc.returncode != 0
        or bool(post_run_errors)
    )
    reason: str
    if wallclock_exceeded:
        reason = "wallclock_exceeded"
    elif not summary.result_seen:
        reason = "no_result_event"
    elif summary.is_error:
        reason = f"claude_error:{summary.subtype or 'unknown'}"
    elif proc.returncode != 0:
        reason = f"nonzero_exit:{proc.returncode}"
    elif post_run_errors:
        reason = f"post_run_error:{post_run_errors[0]}"
    else:
        reason = "success"

    try:
        client.post_audit(
            event_type="agent_run_failed" if failed else "agent_run_complete",
            agent_id=agent_id,
            task_id=session_id,
            payload={
                "session_id": summary.session_id or session_id,
                "model": summary.model or model,
                "tokens": summary.tokens,
                "dollars": summary.dollars,
                "subtype": summary.subtype,
                "is_error": summary.is_error,
                "terminal_reason": summary.terminal_reason,
                "exit_code": proc.returncode,
                "reason": reason,
                "wallclock_exceeded": wallclock_exceeded,
                "permission_denials": summary.permission_denials,
                "errors": summary.errors,
                "post_run_errors": post_run_errors,
            },
        )
    except (httpx.HTTPError, OSError) as exc:
        log.error("post_run_terminal_audit_failed", error=str(exc), reason=reason)

    return RunOutcome(exit_code=1 if failed else 0, summary=summary, reason=reason)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="agents.orchestrator.runner")
    parser.add_argument(
        "--agent",
        required=True,
        help="Agent id under agents/<id>/ (e.g. noop)",
    )
    parser.add_argument(
        "--task",
        default="smoke",
        help=(
            "Task prompt file basename under agents/<agent>/tasks/<task>.md. "
            "Default 'smoke' (the bundled noop agent's task)."
        ),
    )
    args = parser.parse_args(argv)
    try:
        outcome = run_lane(args.agent, task=args.task)
    except ValueError as exc:
        print(f"runner: {exc}", file=sys.stderr)
        return 2
    except FileNotFoundError as exc:
        print(f"runner: {exc}", file=sys.stderr)
        return 2
    return outcome.exit_code


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
