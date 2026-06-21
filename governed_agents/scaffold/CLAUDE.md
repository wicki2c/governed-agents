# CLAUDE.md — operating contract for <YOUR PROJECT>

> Template scaffolded by `governed-agents init`. Replace the placeholders,
> then delete this note.

This file is read first, every session, by any agent working on this repo.
It describes how to OPERATE here — the discipline that does not change as the
project evolves.

## Project purpose (one sentence)
<Describe the one revenue lane / task this project's agents operate.>

## Non-negotiables
1. **No secrets in code, docs, chat, commits, or issues.** Secrets live in
   `.env.local` (gitignored) or a secret manager only. Names go in
   `.env.example`.
2. **No agent ever self-modifies its budget, prompt, proposal-gate config,
   or watchdog rules.** The framework owns those parameters.
3. **Every revenue-generating or external-facing action goes through the
   proposal-gate.** No exceptions, even for "small" actions.
4. **No browser automation against platforms with anti-bot detection.** Use
   the API where one exists; otherwise prepare a packet for the human.
5. **Verification means evidence, not summary.** A task is not done until it
   has been exercised end-to-end with the actual output reported.

## How to run the harness
- `governed-agents serve`      — start the orchestrator (binds 127.0.0.1).
- `governed-agents watchdog`   — start the watchdog (separate process).
- `governed-agents demo`       — watch the proposal-gate loop, no LLM.
- `governed-agents run <agent> <task>` — invoke a runtime agent.
- `governed-agents status` / `scoreboard` — inspect a running orchestrator.

## Adding an agent
Create `agents/<id>/` with a `CLAUDE.md` (scoped operating contract) and a
`tool_allowlist.toml` (default-deny — earn each capability deliberately).
See `agents/example/` for the shape.
