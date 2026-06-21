# `publisher` — custom decision-hook example

The second worked example agent (after `noop`). It demonstrates a **custom
post-approval decision hook**: an approved `publish_note` proposal triggers
[`hooks.py`](hooks.py), which writes the note to a Markdown file in
`workspace/` — a visible, reversible side effect.

```
submit publish_note  →  human approves  →  hooks.publish_note runs  →  workspace/<slug>.md
                         (proposal-gate)     (decision hook)            (the side effect)
```

## Files

| File | Role |
| --- | --- |
| `CLAUDE.md` | the agent's system prompt |
| `tool_allowlist.toml` | default-deny tools + per-run budget |
| `tasks/publish.md` | the task it runs |
| `publish.sh` | submit → poll → record (one Bash entry) |
| `hooks.py` | the `publish_note` decision hook (the new part) |
| `workspace/` | where approved notes are written |

## The hook

`hooks.py` registers a handler for the `publish_note` action_type:

```python
decision_hooks.register("publish_note", publish_note)
```

`publish_note(proposal, session)` sanitises the slug (no path traversal),
writes `OUTPUT_DIR/<slug>.md`, and returns an informational dict. It runs
once, after the decision commits — a hook failure never poisons the decision
(see `infra/decision_hooks.py`).

## Wiring it in

The core ships with **no hooks registered**. Importing `hooks.py` is what
registers `publish_note`, so a running orchestrator must import this module
at startup for the hook to fire. Two ways:

- **Tests / in-process:** import `agents.publisher.hooks` — exactly what
  `tests/test_publisher_hook.py` does, which is why it exercises the hook
  end-to-end with no server change.
- **A real deployment:** import the module from your startup entry point
  (e.g. a one-line `import agents.publisher.hooks` where you launch the app),
  and add `publish_note` to `never_auto_approve` in
  `infra/proposal_policy.toml` so the gate is explicit rather than relying on
  the `[defaults] requires_approval = true` fallback.

## Run it

With an orchestrator running (and the hook wired in):

```bash
./scripts/run_agent.sh publisher     # needs `claude` on PATH
# or drive the loop directly:
ORCHESTRATOR_BASE_URL=http://127.0.0.1:8005 bash agents/publisher/publish.sh
```

Approve the proposal on the dashboard; the note appears in `workspace/`.
Reject it and nothing is written — the gate held.
