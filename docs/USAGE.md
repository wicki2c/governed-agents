# Usage — three worked examples

The [README](../README.md) explains *why* `governed-agents` exists and how to
install it. This guide is the *how*: three end-to-end walkthroughs that take
you from a clean checkout to a custom agent with a real, audited side effect.

Each example builds on the previous one. Read them in order the first time.

- [Prerequisites](#prerequisites)
- [Example 1 — the zero-LLM governance loop](#example-1--the-zero-llm-governance-loop)
- [Example 2 — writing your own agent](#example-2--writing-your-own-agent)
- [Example 3 — running a side effect on approval (decision hook)](#example-3--running-a-side-effect-on-approval-decision-hook)

## Prerequisites

Python 3.12+. You can either install the published package or work from a
source checkout.

```bash
# Option A — install the package (gives you the `governed-agents` CLI on PATH):
pip install governed-agents
# or:  uv pip install governed-agents

# Option B — from a clone, sync deps into a local venv and run the CLI via uv:
uv sync
uv run governed-agents --help
```

The examples below use the `governed-agents` CLI. From a clone, prefix each
command with `uv run` (e.g. `uv run governed-agents demo`); installed, run it
directly (`governed-agents demo`). For the full per-command reference, see
[docs/CLI.md](CLI.md).

Examples 1 and 3 need no Anthropic key. Example 2's "run it for real" step
needs a `claude` binary on your PATH; the rest of it does not.

---

## Example 1 — the zero-LLM governance loop

**Goal:** see the whole proposal → approve → execute → audit loop with no model
in the way. This is the fastest way to understand what the harness actually
does.

The quickest path is the bundled zero-LLM demo, which runs the whole loop
end-to-end in deterministic Python — no socket bind, no Anthropic key, no
interaction needed:

```bash
governed-agents demo
```

To exercise the **interactive** version — a real orchestrator you approve in the
browser — use the demo script, which boots its own orchestrator and waits for
your decision:

```bash
# Boots the orchestrator + submits the demo proposal, then blocks on you.
./scripts/demo.sh
```

`demo.sh` boots the orchestrator on `127.0.0.1:8005`, then submits a
`publish_content` proposal — an **external, irreversible** action that the
proposal policy forces to require human approval. The script then blocks.

```text
  ✋  Proposal <id> is PENDING.
      Open http://127.0.0.1:8005 in your browser and APPROVE or REJECT it.
      (The agent cannot proceed until you decide — that is the point.)
```

Open <http://127.0.0.1:8005>, approve (or reject) the proposal, and the script
prints the resulting audit chain:

```text
demo: audit chain for demo-agent (oldest first):
  <ts>  proposal_submitted
  <ts>  proposal_decided
  <ts>  action_executed
```

What you just exercised:

- **Proposal-gate** — the agent submitted a proposal instead of acting; the
  server forced human approval for an external action.
- **Audit log** — every step (`proposal_submitted`, `proposal_decided`,
  `action_executed`) was recorded in SQLite and mirrored to append-only JSONL.

That is the governance loop. Everything else is agents producing proposals and
deciding which actions deserve a hook.

---

## Example 2 — writing your own agent

**Goal:** add a runtime agent of your own with a least-privilege tool
allowlist. We mirror the bundled `noop` smoke agent
([`agents/noop/`](../agents/noop/)), which is the canonical minimal example.

A runtime agent is a directory under `agents/<id>/` with:

| File | Purpose |
| --- | --- |
| `CLAUDE.md` | the agent's system prompt — what it is, its one job, its tools |
| `tool_allowlist.toml` | **default-deny** tool list + per-run budget ceiling |
| `tasks/<task>.md` | a task the agent runs |
| `workspace/` | scratch space the agent may write to |

### 1. Create the agent directory

```bash
mkdir -p agents/my-agent/tasks agents/my-agent/workspace
touch agents/my-agent/workspace/.gitkeep
```

### 2. Write the allowlist (`agents/my-agent/tool_allowlist.toml`)

Default-deny: anything **not** listed is rejected by Claude Code's permission
system. `agents/orchestrator/runner.py` reads this file to compose the
`claude -p --allowed-tools` argv. Note that the `Bash(...)` pattern matches the
**full command string**, so wrap any multi-step shell in a script and allow
just that one invocation (this is why `noop` ships a `smoke.sh`).

```toml
allowed_tools = [
    "Read(agents/my-agent/**)",
    "Write(agents/my-agent/workspace/**)",
    "Bash(bash run.sh)",
]

# Per-run claude -p budget ceiling, in USD. The runner — not the agent —
# records spend, and the watchdog's dollars_cap is the hard backstop.
max_budget_usd = 0.20
```

### 3. Write the system prompt (`agents/my-agent/CLAUDE.md`)

Keep it small and explicit. State the agent's single job, the tools it may
use, and — most importantly — that **every external or irreversible action
must go through the proposal-gate**, never be performed directly. See
`agents/noop/CLAUDE.md` for the shape.

### 4. Register the agent id (optional)

The server auto-creates a budget-ledger row on first contact, so this is
optional. To pre-register, add your id to `KNOWN_AGENT_IDS` in
[`infra/db.py`](../infra/db.py).

### 5. Run it

```bash
governed-agents run my-agent     # needs `claude` on PATH
```

The runner starts a headless `claude -p` session scoped to your allowlist and
runs it with your agent directory as its working-directory (`cwd`) boundary.
The agent submits proposals per the
[proposal schema](../.claude/skills/proposal-schema/SKILL.md); you approve them
on the dashboard exactly as in Example 1.

---

## Example 3 — running a side effect on approval (decision hook)

**Goal:** make something actually happen when a proposal is approved — a
*decision hook*. The core ships with **no hooks registered**: `dispatch()` is a
no-op for every `action_type` until a deployment adds one. Here we register a
visible, reversible side effect (writing a receipt file) for an approved
`publish_content` action.

A hook is a module-level function with this signature
([`infra/decision_hooks.py`](../infra/decision_hooks.py)):

```python
# infra/hooks_example.py
from __future__ import annotations

from pathlib import Path
from typing import Any

from sqlmodel import Session

from infra import decision_hooks
from infra.db import Proposal


def write_receipt(proposal: Proposal, session: Session) -> dict[str, Any]:
    """Reversible side effect: append a receipt line for an approved action.

    Runs once, AFTER the decision is committed. The returned dict is
    informational only.
    """
    receipt = Path("workspace/receipts.log")
    receipt.parent.mkdir(parents=True, exist_ok=True)
    line = f"{proposal.proposal_id}\t{proposal.action_type}\tapproved\n"
    receipt.write_text(receipt.read_text() + line if receipt.exists() else line)
    return {"receipt": str(receipt)}


# Importing this module registers the hook as a side effect.
decision_hooks.register("publish_content", write_receipt)
```

Three contract details that matter:

1. **Use a module-level function, not a closure.** `register()` stores the
   `(module, name)` pair and re-resolves it lazily on each dispatch, so the
   function must have a real `__module__`/`__name__`. For test closures or
   lambdas, use `register_callable()` instead.
2. **One hook per `action_type`.** `register()` raises on a duplicate rather
   than silently overwriting — import order must not change behaviour.
3. **Importing the module is what registers it.** Ensure your hook module is
   imported during app startup (e.g. import it from your deployment's entry
   point) so registration runs before the first proposal is decided.

### Exercise it

With the hook registered, repeat Example 1. After you approve the
`publish_content` proposal, `dispatch()` fires once and your receipt appears:

```bash
cat workspace/receipts.log
# <proposal_id>	publish_content	approved
```

If a hook raises, the exception does **not** poison the decision: the proposal
stays approved and the orchestrator writes an `audit_flag_decision_hook_error`
event. Hooks are best-effort side effects, never part of the approval itself.

---

## Where to go next

- [`docs/CLI.md`](CLI.md) — the full `governed-agents` CLI reference: every
  subcommand, its options, and an example.
- [`CONTRIBUTING.md`](../CONTRIBUTING.md) — how to contribute (DCO sign-off).
- [`SECURITY.md`](../SECURITY.md) — the dashboard has no auth; keep the
  orchestrator on `127.0.0.1` only.
- `.claude/skills/proposal-schema/SKILL.md` — the authoritative proposal body
  contract every agent must follow.
- [`MERGE_GOVERNANCE.md`](MERGE_GOVERNANCE.md) — why CODEOWNERS can't gate
  merges on a single-maintainer repo, and how to enforce protected-path
  holds in your own merge automation.
