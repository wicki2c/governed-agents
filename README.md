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

Requires Python 3.12+ and [uv](https://docs.astral.sh/uv/).

```bash
# 1. Install deps into a local venv
uv sync

# 2. Start the orchestrator (binds 127.0.0.1:8005) and the watchdog
./scripts/start_orchestrator.sh   # in one terminal
./scripts/start_watchdog.sh       # in another

# 3. Run the zero-LLM demo (no Anthropic key required)
./scripts/demo.sh
```

The demo submits a proposal for an external action, then waits for you to
**approve or reject it** at <http://127.0.0.1:8005>. When you decide, it
prints the resulting audit chain. That's the whole governance loop, with no
model in the way.

To run a real agent (needs Claude Code / a `claude` binary on PATH):

```bash
./scripts/run_agent.sh noop
```

`noop` is a bundled smoke agent that exercises the full
proposal → approve → execute → audit loop.

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
