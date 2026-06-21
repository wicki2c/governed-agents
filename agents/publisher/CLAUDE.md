# publisher runtime agent — custom decision-hook example

This is an **example agent**, not a revenue lane. It is the second worked
example (after `noop`) and exists to demonstrate a **custom post-approval
decision hook** end-to-end: an approved `publish_note` proposal triggers a
hook that writes the note to a file.

The full loop:

    submit publish_note  ->  human approves on dashboard  ->  hooks.publish_note
    runs  ->  note written to workspace/  ->  agent records action_executed

## Your task

Run `bash publish.sh` in this directory (`agents/publisher/`) and then exit.
The script submits a `publish_note` proposal (server-generated id), polls for
the human decision, and on approval records `action_executed`. The
**publish side effect itself is performed server-side by the decision hook**
(`agents/publisher/hooks.py`), not by you — that is the point of the example.

## Tools you are allowed

See `tool_allowlist.toml` (default-deny). You may run `bash publish.sh`, read
your own files, and write `memory.md`. Nothing else. Do not inline the
script's curl calls into your own Bash — that defeats the allowlist.

## What this proves

- A **custom action_type** (`publish_note`) flowing through the proposal-gate.
- A **decision hook** running a visible, reversible side effect on approval.
- The gate holding: a rejected proposal produces no side effect.
- The audit log capturing the chain.
