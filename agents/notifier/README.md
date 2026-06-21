# `notifier` — custom decision-hook example

The third worked example agent (after `noop` and `publisher`). It
demonstrates a **custom post-approval decision hook** with a different
side-effect shape from the publisher's: an approved `notify_on_approval`
proposal triggers [`hooks.py`](hooks.py), which **appends one line** to
`workspace/notifications.log` — a notification-style side effect.

```
submit notify_on_approval  →  human approves  →  hooks.notify_on_approval runs  →  notifications.log
                              (proposal-gate)     (decision hook)                   (the side effect)
```

## Files

| File | Role |
| --- | --- |
| `CLAUDE.md` | the agent's system prompt |
| `tool_allowlist.toml` | default-deny tools + per-run budget |
| `tasks/notify.md` | the task it runs |
| `notify.sh` | submit → poll → record (one Bash entry) |
| `hooks.py` | the `notify_on_approval` decision hook (the new part) |
| `workspace/` | where `notifications.log` is appended (gitignored) |

## The hook

`hooks.py` registers a handler for the `notify_on_approval` action_type:

```python
decision_hooks.register("notify_on_approval", notify_on_approval)
```

`notify_on_approval(proposal, session)` sanitises the message (strips
newlines / control characters so an untrusted payload cannot forge extra log
lines), appends one tab-delimited line to `NOTIFICATIONS_LOG`, and returns an
informational dict. It runs once, after the decision commits — a hook failure
never poisons the decision (see `infra/decision_hooks.py`). The log path is
fixed code; only the line content is payload-derived.

## Wiring it in

The core ships with **no hooks registered**. Importing `hooks.py` is what
registers `notify_on_approval`, so a running orchestrator must import this
module at startup for the hook to fire. Two ways:

- **Tests / in-process:** import `agents.notifier.hooks` — exactly what
  `tests/test_notifier_hook.py` does, which is why it exercises the hook
  end-to-end with no server change.
- **A real deployment:** import the module from your startup entry point
  (e.g. a one-line `import agents.notifier.hooks` where you launch the app),
  and add `notify_on_approval` to `never_auto_approve` in
  `infra/proposal_policy.toml` so the gate is explicit rather than relying on
  the `[defaults] requires_approval = true` fallback.

## Run it

With an orchestrator running (and the hook wired in):

```bash
./scripts/run_agent.sh notifier     # needs `claude` on PATH
# or drive the loop directly:
ORCHESTRATOR_BASE_URL=http://127.0.0.1:8005 bash agents/notifier/notify.sh
```

Approve the proposal on the dashboard; a line appears in
`workspace/notifications.log`. Reject it and nothing is appended — the gate
held.
