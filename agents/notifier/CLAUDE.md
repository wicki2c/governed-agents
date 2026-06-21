# notifier runtime agent — custom decision-hook example

This is an **example agent**, not a revenue lane. It is the third worked
example (after `noop` and `publisher`) and exists to demonstrate a **custom
post-approval decision hook** with a different side-effect shape than the
publisher's: an approved `notify_on_approval` proposal triggers a hook that
appends one line to a notifications log.

The full loop:

    submit notify_on_approval  ->  human approves on dashboard  ->
    hooks.notify_on_approval runs  ->  line appended to notifications.log  ->
    agent records action_executed

## Your task

Run `bash notify.sh` in this directory (`agents/notifier/`) and then exit.
The script submits a `notify_on_approval` proposal (server-generated id),
polls for the human decision, and on approval records `action_executed`. The
**notification side effect itself is performed server-side by the decision
hook** (`agents/notifier/hooks.py`), not by you — that is the point of the
example.

## Tools you are allowed

See `tool_allowlist.toml` (default-deny). You may run `bash notify.sh`, read
your own files, and write `memory.md`. Nothing else. Do not inline the
script's curl calls into your own Bash — that defeats the allowlist.

## What this proves

- A **custom action_type** (`notify_on_approval`) flowing through the
  proposal-gate.
- A **decision hook** whose side effect has a different shape from the
  publisher's (append-to-log instead of write-file) running on approval.
- The gate holding: a rejected proposal produces no side effect.
- The audit log capturing the chain.
