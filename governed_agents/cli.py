"""The `governed-agents` command-line interface.

Design rules (load-bearing — do not relax without an ADR):

* Every subcommand routes to the SAME underlying entrypoint the shell
  scripts wrap (`infra.app`, `infra.watchdog`, `agents.orchestrator.runner`).
  The CLI never reimplements orchestrator / watchdog / runner logic.
* The localhost bind (ADR-0004) is a non-negotiable: `serve` always binds
  `infra.app.UVICORN_HOST` (127.0.0.1). There is deliberately NO
  `--host` / `--bind` / `--address` flag and the host is never read from
  env. `--port` is allowed because a port is not a security boundary.
* Heavy imports (`infra.*`, `agents.*`, `uvicorn`, `httpx`) happen INSIDE
  the handlers, never at module top — so `--help` is fast and free of
  import side effects, and so unit tests can monkeypatch a handler or the
  underlying entrypoint without booting a server or binding a socket.

`main(argv)` parses arguments and dispatches to a named `cmd_*` handler.
Each handler returns an int exit code (or argparse exits 2 on a usage
error). Tests in S3 drive `main()` and patch `uvicorn.run` / the runner /
the watchdog loop to assert the right call (notably `host == "127.0.0.1"`).
"""

from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path

from governed_agents import __version__

# The bundled scaffold template tree. Resolved relative to this file so it
# works from a source checkout AND an installed wheel.
SCAFFOLD_DIR = Path(__file__).resolve().parent / "scaffold"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _default_base_url() -> str:
    """Base URL of a locally-running orchestrator.

    Imports `infra.app` lazily so `--help` and arg-parsing never trigger the
    FastAPI import chain.
    """
    from infra import app

    return f"http://{app.UVICORN_HOST}:{app.UVICORN_PORT}"


def _get_json(url: str) -> int:
    """GET `url`, pretty-print the JSON body, return an exit code.

    A refused connection (orchestrator not running) prints a clean message
    and returns 1 rather than dumping a traceback.
    """
    import json

    import httpx

    try:
        resp = httpx.get(url, timeout=5.0)
    except httpx.ConnectError:
        print(
            f"error: could not connect to {url} — is the orchestrator running?\n"
            "       start it with:  governed-agents serve",
            file=sys.stderr,
        )
        return 1
    except httpx.HTTPError as exc:
        print(f"error: request to {url} failed: {exc}", file=sys.stderr)
        return 1

    if resp.status_code != 200:
        print(
            f"error: {url} returned HTTP {resp.status_code}: {resp.text}",
            file=sys.stderr,
        )
        return 1

    print(json.dumps(resp.json(), indent=2, sort_keys=True))
    return 0


# ---------------------------------------------------------------------------
# Subcommand handlers
# ---------------------------------------------------------------------------


def cmd_init(args: argparse.Namespace) -> int:
    """Scaffold a new governed-agents project into `args.dir`.

    Copies the bundled template tree out. Refuses to overwrite any existing
    file: it lists every collision and exits non-zero, never clobbering.
    """
    target = Path(args.dir).resolve()

    if not SCAFFOLD_DIR.is_dir():
        print(
            f"error: bundled scaffold not found at {SCAFFOLD_DIR}",
            file=sys.stderr,
        )
        return 1

    # First pass: detect collisions before writing anything.
    planned: list[tuple[Path, Path]] = []
    collisions: list[Path] = []
    for src in sorted(SCAFFOLD_DIR.rglob("*")):
        if src.is_dir():
            continue
        rel = src.relative_to(SCAFFOLD_DIR)
        dst = target / rel
        planned.append((src, dst))
        if dst.exists():
            collisions.append(dst)

    if collisions:
        print(
            "error: refusing to overwrite existing files — nothing was written:",
            file=sys.stderr,
        )
        for dst in collisions:
            print(f"  {dst}", file=sys.stderr)
        return 1

    # Second pass: copy.
    for src, dst in planned:
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)

    print(f"scaffolded a new governed-agents project into {target}")
    print("created:")
    for _src, dst in planned:
        print(f"  {dst.relative_to(target)}")
    print("\nnext steps:")
    print("  1. cp .env.example .env.local   # fill in your own secrets")
    print("  2. governed-agents serve        # start the orchestrator")
    print("  3. governed-agents demo         # watch the governance loop")
    return 0


def cmd_serve(args: argparse.Namespace) -> int:
    """Start the orchestrator FastAPI app.

    HARD INVARIANT (ADR-0004): the host is ALWAYS `infra.app.UVICORN_HOST`
    (127.0.0.1). There is no host flag and host is never read from env. Only
    the port may be overridden, because a port is not a security boundary.
    """
    import uvicorn

    from infra import app

    port = args.port if args.port is not None else app.UVICORN_PORT
    print(f"starting orchestrator on http://{app.UVICORN_HOST}:{port}")
    uvicorn.run(
        "infra.app:app",
        host=app.UVICORN_HOST,
        port=port,
        reload=False,
    )
    return 0


def cmd_watchdog(args: argparse.Namespace) -> int:
    """Start the watchdog.

    Delegates to the watchdog module's own entrypoint (`loop()`) rather than
    reimplementing any of its detection logic.
    """
    from infra import watchdog

    watchdog.loop()
    return 0


def cmd_run(args: argparse.Namespace) -> int:
    """Invoke a runtime agent for one task via the orchestrator runner.

    Maps `governed-agents run <agent> <task>` onto the runner's own argparse
    entrypoint (`--agent`, `--task`) so the CLI and the runner stay one
    source of truth.
    """
    from agents.orchestrator import runner

    return runner.main(["--agent", args.agent, "--task", args.task])


def cmd_demo(args: argparse.Namespace) -> int:
    """Run the zero-LLM governance demo."""
    from governed_agents.demo import run_demo

    return run_demo()


def cmd_status(args: argparse.Namespace) -> int:
    """Pretty-print `<url>/status` from a running orchestrator."""
    base = args.url if args.url is not None else _default_base_url()
    return _get_json(f"{base.rstrip('/')}/status")


def cmd_scoreboard(args: argparse.Namespace) -> int:
    """Pretty-print `<url>/scoreboard` from a running orchestrator."""
    base = args.url if args.url is not None else _default_base_url()
    return _get_json(f"{base.rstrip('/')}/scoreboard")


# ---------------------------------------------------------------------------
# Parser
# ---------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="governed-agents",
        description=(
            "CLI for the governed-agents harness: a localhost-only "
            "proposal-gate orchestrator, a watchdog, and a runtime-agent "
            "runner."
        ),
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"%(prog)s {__version__}",
    )
    sub = parser.add_subparsers(dest="command", metavar="<command>")
    sub.required = True

    p_init = sub.add_parser(
        "init",
        help="scaffold a new governed-agents project",
        description="Scaffold a new governed-agents project. Refuses to overwrite existing files.",
    )
    p_init.add_argument(
        "dir",
        nargs="?",
        default=".",
        help="target directory for the new project (default: current dir)",
    )
    p_init.set_defaults(func=cmd_init)

    p_serve = sub.add_parser(
        "serve",
        help="start the orchestrator (binds 127.0.0.1 only)",
        description=(
            "Start the orchestrator FastAPI app. Always binds 127.0.0.1 "
            "(ADR-0004); there is no host flag by design."
        ),
    )
    p_serve.add_argument(
        "--port",
        type=int,
        default=None,
        help="port to bind (default: infra.app.UVICORN_PORT / $ORCHESTRATOR_PORT / 8005)",
    )
    p_serve.set_defaults(func=cmd_serve)

    p_watchdog = sub.add_parser(
        "watchdog",
        help="start the watchdog",
        description="Start the watchdog process (delegates to infra.watchdog.loop).",
    )
    p_watchdog.set_defaults(func=cmd_watchdog)

    p_run = sub.add_parser(
        "run",
        help="invoke a runtime agent for one task",
        description="Invoke a runtime agent for one task via the orchestrator runner.",
    )
    p_run.add_argument("agent", help="agent id under agents/<agent>/ (e.g. noop)")
    p_run.add_argument(
        "task",
        nargs="?",
        default="smoke",
        help="task basename under agents/<agent>/tasks/<task>.md (default: smoke)",
    )
    p_run.set_defaults(func=cmd_run)

    p_demo = sub.add_parser(
        "demo",
        help="run the zero-LLM governance demo",
        description=(
            "Run the zero-LLM governance demo: submit a proposal, show it "
            "blocked by the gate, approve it, and print the audit trail."
        ),
    )
    p_demo.set_defaults(func=cmd_demo)

    p_status = sub.add_parser(
        "status",
        help="GET /status from a running orchestrator",
        description="Fetch and pretty-print /status from a running orchestrator.",
    )
    p_status.add_argument(
        "--url",
        default=None,
        help="orchestrator base URL (default: http://127.0.0.1:<UVICORN_PORT>)",
    )
    p_status.set_defaults(func=cmd_status)

    p_scoreboard = sub.add_parser(
        "scoreboard",
        help="GET /scoreboard from a running orchestrator",
        description="Fetch and pretty-print /scoreboard from a running orchestrator.",
    )
    p_scoreboard.add_argument(
        "--url",
        default=None,
        help="orchestrator base URL (default: http://127.0.0.1:<UVICORN_PORT>)",
    )
    p_scoreboard.set_defaults(func=cmd_scoreboard)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
