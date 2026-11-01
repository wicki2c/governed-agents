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

## Lean setup (Chad, 1 Nov 2026; standing rule, the only name for it)
1. Each PR or release gets small targeted tests. Never run the full test suite on every change.
2. Before every push, run the affected tests locally. CI is only the backstop. Put the local test results in the PR description.
3. Keep entry rules lean and read with low context: no generated output in repos, no reading whole large files.
4. PR CI is tiered: a fast tier plus the affected suites, a lean repo guard, and paging when main goes red.
5. Testing stays on Chad's Mac (Colima "ci" pool) plus the one Hetzner CPX32. No new hardware, and no pay-per-minute overflow without Chad's approval.
6. Every repo and project, including new ones, starts on the lean setup.
7. Only the projects listed in ~/.local/share/driver/FLEET-INTENT.md run (1 Nov: Universe and Talent Lockr, both on Claude). Everything else stays paused until Chad says otherwise. Re-read that file after every restart.
