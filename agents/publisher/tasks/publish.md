# Task: publish a note (gated)

Publish a short note to the site by going through the proposal-gate.

1. Run `bash publish.sh`. It submits a `publish_note` proposal with a
   `slug` and `body`, then polls for the human decision.
2. On **approval**, the orchestrator's `publish_note` decision hook writes
   the note to `workspace/<slug>.md`. Record `action_executed` (the script
   does this) and exit.
3. On **rejection**, do nothing further — the gate held; no note is
   published. Exit cleanly.

Do not attempt to write the note yourself. The publish is a governed side
effect that only runs after a human approves.
