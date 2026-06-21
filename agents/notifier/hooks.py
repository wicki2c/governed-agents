"""Example post-approval decision hook for the `notifier` agent.

When a `notify_on_approval` proposal is APPROVED, this hook appends one line
to a local notifications log — a notification-style side effect with a
different shape from the publisher's write-a-file hook (append-to-log instead
of write-file). It is the third worked example (see README.md) and
demonstrates the decision-hook contract from `infra/decision_hooks.py`
end-to-end:

    proposal (notify_on_approval) -> human approval -> this hook runs
        -> one line appended to notifications.log

Importing this module registers the hook as a side effect. The core ships
with no hooks; to activate this one in a running orchestrator, ensure this
module is imported at startup (see README.md, "Wiring it in"). Tests import
it in-process, which is why the end-to-end test fires the hook without any
server-side change. As with every hook, a failure here never poisons the
decision (the dispatch site wraps the call and records
`audit_flag_decision_hook_error`).
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlmodel import Session

from infra import decision_hooks
from infra.db import Proposal

ACTION_TYPE = "notify_on_approval"

# Append target for approval notifications. A deployment points this at a real
# log; the test monkeypatches it to a tmp path. Read at call time so it stays
# overridable. The PATH is fixed code — never influenced by proposal payload;
# only the line CONTENT comes from the (sanitised) payload.
NOTIFICATIONS_LOG = Path("agents/notifier/workspace/notifications.log")

# Cap on a single notification line. A bounded length keeps an untrusted
# payload from bloating the log with one enormous entry.
_MAX_MESSAGE_LEN = 200


def _sanitize(message: str) -> str:
    """Reduce an untrusted message to a single safe log fragment.

    Strips newlines, carriage returns, and other control characters so a
    crafted payload cannot forge extra log lines (a log-injection guard — the
    notifier's analog of the publisher's slug path-safety), then caps the
    length. The fragment is the only payload-derived part of a line; the file
    path is never influenced by the payload.
    """
    cleaned = "".join(ch for ch in message if ch.isprintable() and ch not in "\r\n")
    return cleaned[:_MAX_MESSAGE_LEN]


def notify_on_approval(proposal: Proposal, session: Session) -> dict[str, Any]:
    """Append one notification line for an approved proposal.

    Runs once, after the decision commits. The returned dict is
    informational only (see the dispatch contract). Reversible: trim the line
    from the log to undo the effect.
    """
    payload = proposal.payload
    if isinstance(payload, str):
        payload = json.loads(payload or "{}")
    message = _sanitize(str(payload.get("message", "A governed action was approved.")))

    line = f"{datetime.now(UTC).isoformat()}\t{ACTION_TYPE}\t{proposal.proposal_id}\t{message}\n"

    path = NOTIFICATIONS_LOG
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(line)
    return {"notified": str(path), "proposal_id": proposal.proposal_id}


# Importing this module wires the hook into the dispatch table.
decision_hooks.register(ACTION_TYPE, notify_on_approval)
