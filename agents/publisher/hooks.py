"""Example post-approval decision hook for the `publisher` agent.

When a `publish_note` proposal is APPROVED, this hook writes the note to a
Markdown file — a visible, reversible side effect (delete the file to undo).
It is the second worked example (see README.md) and demonstrates the
decision-hook contract from `infra/decision_hooks.py` end-to-end:

    proposal (publish_note) -> human approval -> this hook runs -> file on disk

Importing this module registers the hook as a side effect. The core ships
with no hooks; to activate this one in a running orchestrator, ensure this
module is imported at startup (see README.md, "Wiring it in"). Tests import
it in-process, which is why the end-to-end test fires the hook without any
server-side change.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from sqlmodel import Session

from infra import decision_hooks
from infra.db import Proposal

ACTION_TYPE = "publish_note"

# Output location for approved notes. A deployment points this at a real
# directory; the test monkeypatches it to a tmp dir. Read at call time so it
# stays overridable.
OUTPUT_DIR = Path("agents/publisher/workspace")


def _safe_slug(raw: str) -> str:
    """Reduce an untrusted slug to a safe filename stem — no path
    traversal, no separators. A governance example must not let proposal
    payload escape the output directory."""
    cleaned = re.sub(r"[^a-zA-Z0-9_-]", "-", raw).strip("-")
    return cleaned[:64] or "note"


def publish_note(proposal: Proposal, session: Session) -> dict[str, Any]:
    """Write an approved note to ``OUTPUT_DIR/<slug>.md``.

    Runs once, after the decision commits. The returned dict is
    informational only (see the dispatch contract). Reversible: deleting the
    file undoes the effect.
    """
    payload = proposal.payload
    if isinstance(payload, str):
        payload = json.loads(payload or "{}")
    slug = _safe_slug(str(payload.get("slug") or proposal.proposal_id))
    body = str(payload.get("body", ""))

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    path = OUTPUT_DIR / f"{slug}.md"
    path.write_text(body, encoding="utf-8")
    return {"published": str(path), "bytes": len(body)}


# Importing this module wires the hook into the dispatch table.
decision_hooks.register(ACTION_TYPE, publish_note)
