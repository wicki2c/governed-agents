# infra/ — Orchestration server

The FastAPI orchestration server and its watchdog. This is the single
source of truth for proposals, budgets, and the audit log.

- `app.py` — FastAPI app: proposal-gate, budget ledger, audit log,
  scoreboard, status, admin pause-all, credentials proxy. Binds 127.0.0.1
  only; no auth (localhost is the auth — do not expose to a network).
- `db.py` — SQLModel schemas + the SQLite engine. Audit rows are mirrored
  to an append-only JSONL file so the trail survives DB corruption.
- `watchdog.py` — a separate process that pauses stuck / over-budget /
  looping agents without the agent's cooperation.
- `credentials_proxy.py` — one-shot, scoped, time-bounded tokens so agents
  never hold raw credentials.
- `decision_hooks.py` — post-approval hook dispatch table (the plugin seam
  for side effects that run when a proposal is approved).
- `proposal_policy.toml` — the auto-approve / never-auto-approve policy.
- `dashboard.html` — single-page localhost dashboard.
