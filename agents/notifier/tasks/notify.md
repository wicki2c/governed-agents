# Task: emit a notification (gated)

Emit a notification by going through the proposal-gate.

1. Run `bash notify.sh`. It submits a `notify_on_approval` proposal with a
   `message`, then polls for the human decision.
2. On **approval**, the orchestrator's `notify_on_approval` decision hook
   appends one line to the notifications log server-side. Record
   `action_executed` (the script does this) and exit.
3. On **rejection**, do nothing further — the gate held; nothing is emitted.
   Exit cleanly.

Do not attempt to perform the notification yourself. Emitting it is a
governed side effect that only runs after a human approves.
