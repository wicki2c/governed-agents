# Task: noop smoke

You are the noop runtime agent. Execute the framework smoke test.

Your one job: invoke `Bash(bash smoke.sh)` exactly once. The script
mints the proposal UUID, submits, polls for human decision, and posts
the full audit chain (proposal_submitted via the server, then
action_executed + agent_stop on approval) — all via curl to
`http://127.0.0.1:8005`.

Do not inline any of the script's commands into your own Bash calls.
Claude Code's Bash permission matches the full command string; the
allowlist permits `Bash(bash smoke.sh)` exactly, not the inner curls or
`uuidgen` invocations. The script is the smallest unit the allowlist
will authorise.

Do not write files outside `agents/noop/`. Do not call any tool other
than `Read(agents/noop/**)` (optional, prior runs) and `Bash(bash smoke.sh)`.
Exit after the script returns.
