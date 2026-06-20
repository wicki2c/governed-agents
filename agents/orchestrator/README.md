# Runtime agent: orchestrator

The orchestrator runtime agent (`runner.py` + `CLAUDE.md`) wraps a single
`claude -p` invocation for one agent and one task. It is the trust boundary
between an agent and the orchestration server: it loads the agent's
default-deny tool allowlist, refuses to start a paused agent, runs the
child with a wall-clock deadline, and posts the budget charge from the
observed `result` usage (the agent never charges itself).

Run an agent:

```
./scripts/run_agent.sh <agent-id> [--task <task>]
```

The bundled `noop` agent is a zero-cost smoke. Add your own agents under
`agents/<agent-id>/` with a `CLAUDE.md`, a `tool_allowlist.toml`
(default-deny), and `tasks/<task>.md`.
