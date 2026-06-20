# noop — framework smoke agent

The **framework smoke agent**, not a real workload. It exists to prove the
full proposal → approve → execute → audit loop end-to-end.

To run (requires Claude Code / a `claude` binary on PATH):

```
./scripts/run_agent.sh noop
```

That invokes `agents/orchestrator/runner.py --agent noop`, which shells out
to `claude -p` against `agents/noop/CLAUDE.md`.

For a **zero-LLM** walkthrough of the same governance loop (no Anthropic key
needed), run `./scripts/demo.sh` instead.

See:
- `agents/noop/CLAUDE.md` — system prompt
- `agents/noop/tasks/smoke.md` — the task definition
- `agents/noop/tool_allowlist.toml` — the default-deny tool allowlist
- `agents/noop/memory.md` — append-only run log (created on first run)
