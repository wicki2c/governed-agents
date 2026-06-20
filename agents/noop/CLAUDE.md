# noop runtime agent — framework smoke

This is the **smoke test agent for the framework**, not a revenue lane.
It exists only to prove the proposal → approve → execute → audit loop
end-to-end. Do not extend it with real work.

## Your task

You have exactly one job: run `bash smoke.sh` in your current working
directory (`agents/noop/`) and then exit.

That's it. The script handles everything: minting a proposal UUID,
submitting the proposal to the orchestrator, polling for the human
decision, posting the `action_executed` audit event on approval, and
writing a paragraph to `memory.md`.

## Why a script, not a multi-step plan

Claude Code's Bash permission system matches the FULL shell command
against the allowlist patterns. A multi-step plan like
`PID="$(uuidgen)" && curl ...` doesn't match `Bash(uuidgen)` alone, so
those calls would all be denied. The wrapper script collapses the
workflow into one `Bash(bash smoke.sh)` invocation, which matches
cleanly.

## Tools you are allowed

See `tool_allowlist.toml` in this directory:
- `Read(agents/noop/**)` — read your own files
- `Write(agents/noop/memory.md)` + `Edit(agents/noop/memory.md)` — the
  script appends to memory.md, but it's listed here so you can read
  prior runs if useful
- `Bash(bash smoke.sh)` — the only Bash you should invoke

You may not edit ANY file outside `agents/noop/`. You may not invoke
other tools. Your `--add-dir` boundary is enforced separately.

## Exact procedure

1. (Optional) `Read(agents/noop/memory.md)` to see prior runs.
2. `Bash(bash smoke.sh)` — runs the smoke; blocks until decision or
   timeout (5 minutes max). The script handles all curl calls.
3. Exit. The script has already written the audit chain and memory.md.

Do not call any other tools. Do not "improve" the script. Do not
inline commands from the script into your own Bash calls — that
defeats the permission allowlist.

## What this proves

- Proposal submission + server-side policy enforcement
- Human-in-the-loop approval through the dashboard
- Audit log captures the full chain
- Budget ledger records the run (via the runner, not you)
- Watchdog can pause you if you misbehave
