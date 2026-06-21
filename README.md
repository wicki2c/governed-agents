# Governed Agents

**A governance harness for autonomous AI agents.** Budget caps, a human
proposal-gate on every external action, an independent watchdog, and a
complete audit trail — the controls you need before you let an agent touch
the real world.

> Status: early / experimental (v0). The core is open-source and stable
> enough to build on; the API may still change before 1.0.

---

## Why

Autonomous agents are easy to start and hard to *trust*. The moment an agent
can spend money, publish content, send email, or call a third-party API, you
need answers to four questions:

1. **What is it about to do?** — and can a human stop it first?
2. **How much has it spent?** — and what happens when it hits the cap?
3. **What if it gets stuck or loops?** — who pulls the plug?
4. **What did it actually do?** — is there a trail you can audit?

Most agent frameworks optimize for capability. This one optimizes for
**governance** — the layer that makes autonomy safe to ship. It is small,
dependency-light, and unopinionated about what your agents *do*.

## What you get

Five primitives, each a small, readable module:

| Primitive | What it does | Where |
| --- | --- | --- |
| **Proposal-gate** | Every external-facing or irreversible action is a *proposal* a human approves on a dashboard. Agents physically cannot self-approve a `never_auto_approve` action — the server rejects and audit-flags the attempt. | `infra/app.py`, `infra/proposal_policy.toml` |
| **Budget ledger** | Per-agent token + dollar caps. The runner (not the agent) records spend, so an agent can't under-report itself out of a cap. Over-cap → auto-paused. | `infra/db.py`, `infra/app.py` |
| **Watchdog** | A separate process that pauses stuck, over-budget, or looping agents *without the agent's cooperation*. | `infra/watchdog.py` |
| **Audit log** | Every proposal, decision, charge, and run is recorded in SQLite and mirrored to append-only JSONL, so the trail survives DB loss. | `infra/db.py`, `infra/app.py` |
| **Default-deny tool allowlists** | Each agent gets an explicit per-agent allowlist; anything not listed is denied. The runner composes the `claude -p` permission set from it. | `agents/<id>/tool_allowlist.toml`, `agents/orchestrator/runner.py` |

Plus a flat **multi-agent orchestrator** (`agents/orchestrator/runner.py`)
that runs one agent per task under all of the above, and a single-page
**localhost dashboard** for approvals and live state.

## Architecture

```
┌──────────────────────────────────────────────────────────────┐
│  Your runtime agents  (agents/<id>/)                         │
│  one headless `claude -p` session each, default-deny tools   │
└───────────────▲──────────────────────────────────────────────┘
                │ HTTP (localhost only)
┌───────────────┴──────────────────────────────────────────────┐
│  Orchestration server  (infra/)                              │
│  proposal-gate · budget ledger · audit log · scoreboard      │
│  + watchdog (separate process) + credentials proxy           │
│  FastAPI + SQLite · binds 127.0.0.1 only · no auth           │
└──────────────────────────────────────────────────────────────┘
```

The server is the single source of truth. Agents never hold raw
credentials (they request one-shot scoped tokens from the proxy) and never
write their own budget charges.

## Quickstart

Requires Python 3.12+.

```bash
pip install governed-agents
```

> **Until the first PyPI release**, install straight from the repo — same
> `governed-agents` CLI, same UX:
>
> ```bash
> pip install git+https://github.com/wicki2c/governed-agents
> # or, from a clone:  pip install .
> ```
>
> Prefer [uv](https://docs.astral.sh/uv/)? `uv pip install governed-agents`
> drops it into the current environment, or `uv add governed-agents` adds it
> to a project.

Installing puts a single `governed-agents` command on your `PATH`. The fastest
way to see what the harness does is the zero-LLM demo — **no Anthropic key, no
server, no setup**:

```bash
governed-agents demo
```

It submits a proposal for an external action, shows the gate **block** it,
approves it, executes it, and prints the resulting audit chain — the whole
governance loop, deterministic and in-memory, with no model in the way.

### Run the live loop

To approve a proposal yourself in the browser, start the orchestrator and the
watchdog in two terminals, then run an agent:

```bash
# Terminal 1 — orchestrator (binds 127.0.0.1 only; never networked):
governed-agents serve

# Terminal 2 — independent watchdog:
governed-agents watchdog

# Terminal 3 — run the bundled noop smoke agent:
governed-agents run noop
```

`serve` opens the localhost dashboard at <http://127.0.0.1:8005> where you
approve or reject the proposal; when you decide, the agent finishes and the run
is recorded. `noop` is a bundled zero-cost smoke agent that exercises the full
proposal → approve → execute → audit loop. Running a *real* agent needs a
`claude` binary on your `PATH`.

Two read-only commands report live state from a running orchestrator:

```bash
governed-agents status       # orchestrator health + live counts
governed-agents scoreboard   # per-agent spend, runs, and pauses
```

### Scaffold your own project

```bash
governed-agents init my-project
```

`init` writes a fresh project tree (agents, allowlists, tasks, config) and is
**no-clobber** — if anything it would write already exists, it lists the
collisions and refuses to touch your files.

For the full command and flag reference, see **[docs/CLI.md](docs/CLI.md)**.

### From source (for contributors)

To hack on the harness itself, work from a checkout with
[uv](https://docs.astral.sh/uv/):

```bash
git clone https://github.com/wicki2c/governed-agents
cd governed-agents
uv sync                          # install deps into a local venv

# Run the same CLI without installing — just prefix with `uv run`:
uv run governed-agents demo
uv run governed-agents serve     # binds 127.0.0.1 only
```

The bundled `./scripts/*.sh` wrappers (`demo.sh`, `start_orchestrator.sh`,
`start_watchdog.sh`, `run_agent.sh`) are still there and wrap the same
entrypoints.

## Writing your own agent

1. Create `agents/<your-agent>/` with a `CLAUDE.md` (system prompt), a
   `tool_allowlist.toml` (default-deny — list only the tools it may use),
   and `tasks/<task>.md`.
2. Add its id to `KNOWN_AGENT_IDS` in `infra/db.py` (or just let the server
   auto-create a ledger row on first contact).
3. Have it submit proposals per `.claude/skills/proposal-schema/SKILL.md`.
4. To run a side effect when a proposal is approved, register a hook with
   `decision_hooks.register(action_type, handler)` — see
   `infra/decision_hooks.py`.

For step-by-step worked examples — the zero-LLM demo loop, a custom agent,
and a decision hook end-to-end — see **[docs/USAGE.md](docs/USAGE.md)**.

## Verify

```bash
./scripts/verify.sh   # secrets scan + lint + format + tests + smoke
uv run pytest -q      # tests only
```

## Provenance

This harness is not a paper design. It was built and exercised end-to-end
across four real, money-touching autonomous-agent workloads in a controlled
experiment — running live sites, payment test-mode checkouts, and
third-party API integrations under exactly these controls. The governance
layer is what survived and proved worth keeping; this repo is that layer,
extracted clean.

## Roadmap

`governed-agents` is the open-source core. A managed/premium layer (hosted
control plane, team features, compliance reporting) may follow **once the
core shows real adoption** — deliberately not before. The core will stay
open-source and useful on its own.

## Contributing & security

- Contributions welcome — see [CONTRIBUTING.md](CONTRIBUTING.md) (DCO
  sign-off, no CLA).
- Found a vulnerability? See [SECURITY.md](SECURITY.md). **Note:** the
  dashboard has no authentication and is meant for `127.0.0.1` only — never
  expose the orchestrator port to a network.
- Be excellent to each other: [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md).

## License

[Apache-2.0](LICENSE). See [NOTICE](NOTICE) for bundled third-party
attributions.
