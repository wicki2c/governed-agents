# `governed-agents` — CLI reference

The `governed-agents` command-line interface is a thin, single source-of-truth
wrapper over the harness: a localhost-only proposal-gate orchestrator, an
independent watchdog, and a runtime-agent runner. Every subcommand routes to the
same underlying entrypoint the shell scripts wrap — the CLI never reimplements
orchestrator, watchdog, or runner logic.

For *why* the harness exists and a conceptual tour, read the
[README](../README.md). For end-to-end worked walkthroughs (the zero-LLM loop, a
custom agent, a decision hook), read [docs/USAGE.md](USAGE.md).

- [Install](#install)
- [Synopsis](#synopsis)
- [`init`](#init) · [`serve`](#serve) · [`watchdog`](#watchdog) ·
  [`run`](#run) · [`demo`](#demo) · [`status`](#status) ·
  [`scoreboard`](#scoreboard)
- [See also](#see-also)

## Install

Requires Python 3.12+.

```bash
pip install governed-agents
# or, with uv:
uv pip install governed-agents
```

This installs the `governed-agents` executable on your `PATH`. From a source
checkout you can run the same CLI without installing — `uv run governed-agents
<command>` — which is what the examples in [docs/USAGE.md](USAGE.md) use.

## Synopsis

```text
governed-agents [--version] <command> ...
```

| Command | Purpose |
| --- | --- |
| [`init`](#init) | Scaffold a new governed-agents project. |
| [`serve`](#serve) | Start the orchestrator (binds **127.0.0.1 only**). |
| [`watchdog`](#watchdog) | Start the watchdog. |
| [`run`](#run) | Invoke a runtime agent for one task. |
| [`demo`](#demo) | Run the zero-LLM governance demo. |
| [`status`](#status) | Pretty-print `/status` from a running orchestrator. |
| [`scoreboard`](#scoreboard) | Pretty-print `/scoreboard` from a running orchestrator. |

Global options:

| Option | Effect |
| --- | --- |
| `-h`, `--help` | Show help (works on the top level and on every subcommand). |
| `--version` | Print the installed version and exit. |

Every subcommand also accepts `-h` / `--help`. `--help` is import-side-effect
free — it never boots a server or binds a socket — so it is always safe and fast
to run.

---

## `init`

Scaffold a new governed-agents project from the bundled template tree. It is
**no-clobber**: if any file it would write already exists, it lists every
collision and exits non-zero *without writing anything*.

**Synopsis**

```text
governed-agents init [dir]
```

**Arguments**

| Argument | Required | Description |
| --- | --- | --- |
| `dir` | no | Target directory for the new project. Default: the current directory (`.`). |

**Example**

```bash
governed-agents init my-project
# scaffolded a new governed-agents project into /…/my-project
# created:
#   …
# next steps:
#   1. cp .env.example .env.local   # fill in your own secrets
#   2. governed-agents serve        # start the orchestrator
#   3. governed-agents demo         # watch the governance loop
```

---

## `serve`

Start the orchestrator FastAPI app.

The orchestrator **always binds `127.0.0.1`** and there is deliberately **no
`--host` / `--bind` / `--address` flag** — the localhost-only bind is a
non-negotiable security boundary (ADR-0004). The dashboard has no
authentication; keeping it on the loopback interface is what protects it. The
host is never read from the environment either. Only the port may be overridden,
because a port is not a security boundary.

**Synopsis**

```text
governed-agents serve [--port PORT]
```

**Options**

| Option | Description |
| --- | --- |
| `--port PORT` | Port to bind. Default: `infra.app.UVICORN_PORT` (`$ORCHESTRATOR_PORT`, else `8005`). |

**Example**

```bash
governed-agents serve
# starting orchestrator on http://127.0.0.1:8005

# or pick a different port (still 127.0.0.1):
governed-agents serve --port 9000
# starting orchestrator on http://127.0.0.1:9000
```

> The bind host cannot be changed by design. Never expose the orchestrator port
> to a network — see [SECURITY.md](../SECURITY.md).

---

## `watchdog`

Start the watchdog as a standalone process. It runs independently of the
orchestrator and can pause stuck, over-budget, or looping agents *without the
agent's cooperation*. Run it alongside `serve` (in a separate terminal or
process).

**Synopsis**

```text
governed-agents watchdog
```

This subcommand takes no options beyond `--help`. The poll interval is read from
the environment (`$WATCHDOG_INTERVAL_SECONDS`, default `60`).

**Example**

```bash
governed-agents watchdog
# starting watchdog (polling every 60s)
```

---

## `run`

Invoke a single runtime agent for one task via the orchestrator runner. The
runner starts a headless `claude -p` session scoped to the agent's default-deny
`tool_allowlist.toml`, with the agent directory as its working-directory
boundary, and records the budget charge from the parent runner — the agent never
charges itself. (Running a real agent needs a `claude` binary on your `PATH`.)

**Synopsis**

```text
governed-agents run <agent> [task]
```

**Arguments**

| Argument | Required | Description |
| --- | --- | --- |
| `agent` | yes | Agent id under `agents/<agent>/` (e.g. `noop`). |
| `task` | no | Task basename under `agents/<agent>/tasks/<task>.md`. Default: `smoke`. |

**Example**

```bash
# Run the bundled noop smoke agent (its default `smoke` task):
governed-agents run noop

# Run a named task for your own agent:
governed-agents run my-agent publish
```

`noop` is a bundled zero-cost smoke agent that exercises the full
proposal → approve → execute → audit loop. See
[docs/USAGE.md](USAGE.md#example-2--writing-your-own-agent) for writing your own.

---

## `demo`

Run the zero-LLM governance demo: submit a proposal, see it blocked by the gate,
approve it, watch it execute, and print the audit trail — with **no model in the
way**. It runs in pure, deterministic Python against an in-memory database (no
real socket bind, no Anthropic key, no paid layer), so it never touches a real
deployment's database or audit log.

**Synopsis**

```text
governed-agents demo
```

This subcommand takes no options beyond `--help`.

**Example**

```bash
governed-agents demo
```

> For the *interactive* version of this loop — a real orchestrator on
> `127.0.0.1:8005` that waits for you to approve a proposal in the browser — see
> [Example 1 in docs/USAGE.md](USAGE.md#example-1--the-zero-llm-governance-loop).

---

## `status`

Fetch and pretty-print `/status` from a running orchestrator. If the
orchestrator is not running, it prints a clean message (and a hint to start it
with `governed-agents serve`) and exits non-zero — no traceback.

**Synopsis**

```text
governed-agents status [--url URL]
```

**Options**

| Option | Description |
| --- | --- |
| `--url URL` | Orchestrator base URL. Default: `http://127.0.0.1:<UVICORN_PORT>`. |

**Example**

```bash
# Against the default local orchestrator:
governed-agents status

# Against a non-default port:
governed-agents status --url http://127.0.0.1:9000
```

---

## `scoreboard`

Fetch and pretty-print `/scoreboard` from a running orchestrator — the per-agent
live state (spend, runs, pauses). Same connection handling as `status`.

**Synopsis**

```text
governed-agents scoreboard [--url URL]
```

**Options**

| Option | Description |
| --- | --- |
| `--url URL` | Orchestrator base URL. Default: `http://127.0.0.1:<UVICORN_PORT>`. |

**Example**

```bash
governed-agents scoreboard
```

---

## See also

- [README.md](../README.md) — why the harness exists, the five primitives, and
  the quickstart.
- [docs/USAGE.md](USAGE.md) — three end-to-end worked examples.
- [docs/MERGE_GOVERNANCE.md](MERGE_GOVERNANCE.md) — governing merges on a
  single-maintainer repo.
- [SECURITY.md](../SECURITY.md) — the dashboard has no auth; keep the
  orchestrator on `127.0.0.1` only.
