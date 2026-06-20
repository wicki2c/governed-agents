# CLAUDE.md — Governed Agents

Operating notes for anyone (human or AI) working in this repo.

## What this is
The open-source governance core for autonomous AI agents: a human
proposal-gate, per-agent budget caps, an independent watchdog, a full audit
trail, default-deny tool allowlists, and a flat multi-agent orchestrator.
See README.md.

## Non-negotiables
1. **No secrets** in code, tests, docs, commits, or issues. Real values live
   in `.env.local` (gitignored) only.
2. **No agent self-approves** a `never_auto_approve` action, and **no agent
   writes its own budget charge.** The framework owns those paths; changes
   there require tests.
3. **The dashboard is localhost-only with no auth.** Never bind it to a
   network or expose the port.
4. **Evidence, not summary.** Show the test output / curl / behavior.

## Layout
- `infra/` — orchestration server (FastAPI + SQLite), watchdog, credentials
  proxy, decision-hook dispatch, proposal policy, dashboard.
- `agents/orchestrator/` — the runner that wraps one `claude -p` per task.
- `agents/noop/` — bundled smoke / demo agent.
- `.claude/skills/` — the contracts runtime agents read (proposal schema,
  budget check, stop conditions).
- `tests/` — the governance guarantees, expressed as tests.

## Working in this repo
- `uv sync` to set up.
- `./scripts/verify.sh` before a PR (secrets scan + ruff + pytest + smoke).
- `uv run pytest -q` for tests only.
- Sign off commits (DCO): `git commit -s`. See CONTRIBUTING.md.
