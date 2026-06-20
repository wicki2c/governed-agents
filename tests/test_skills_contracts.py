"""Drift catchers: the three SKILL.md files in `.claude/skills/` are
contracts that runtime agents read. If they fall out of sync with the
server-side enforcement (`infra/proposal_policy.toml`, `BudgetCharge`
pydantic model, watchdog rule names), agents will violate the contract
and the framework will fail in subtle ways.

These tests assert the SKILL.md files mention every server-side
constant by name. They are deliberately lax on prose — they only check
literal presence of action_type names, field names, and rule names.
"""

from __future__ import annotations

import tomllib
from pathlib import Path

from infra.app import BudgetCharge
from infra.watchdog import PauseReason

REPO_ROOT = Path(__file__).resolve().parent.parent
SKILL_DIR = REPO_ROOT / ".claude" / "skills"
POLICY_PATH = REPO_ROOT / "infra" / "proposal_policy.toml"


def _read_skill(name: str) -> str:
    return (SKILL_DIR / name / "SKILL.md").read_text(encoding="utf-8")


def _policy_lists() -> tuple[set[str], set[str]]:
    data = tomllib.loads(POLICY_PATH.read_text(encoding="utf-8"))
    auto = set(data.get("auto_approve", {}).get("action_types", []))
    never = set(data.get("never_auto_approve", {}).get("action_types", []))
    return auto, never


def test_proposal_schema_skill_lists_every_policy_action_type() -> None:
    text = _read_skill("proposal-schema")
    auto, never = _policy_lists()
    for action_type in auto | never:
        assert action_type in text, (
            f"proposal-schema/SKILL.md is missing action_type {action_type!r} "
            f"that appears in infra/proposal_policy.toml"
        )


def test_proposal_schema_skill_mentions_audit_flag_event() -> None:
    text = _read_skill("proposal-schema")
    # This event is what the server writes when an agent self-approves a
    # never_auto_approve action_type. Agents must know it exists so they
    # don't keep retrying.
    assert "audit_flag_self_approve_attempt" in text


def test_budget_check_skill_mentions_every_BudgetCharge_field() -> None:
    text = _read_skill("budget-check")
    for field_name in BudgetCharge.model_fields.keys():
        assert field_name in text, (
            f"budget-check/SKILL.md is missing field {field_name!r} from infra.app.BudgetCharge"
        )


def test_budget_check_skill_mentions_DEFAULT_RATE_constant() -> None:
    text = _read_skill("budget-check")
    # The skill should reference DEFAULT_DOLLARS_PER_1K_TOKENS so agents
    # know the dollars derivation when they omit `dollars`.
    assert "DEFAULT_DOLLARS_PER_1K_TOKENS" in text


def test_stop_conditions_skill_lists_every_watchdog_PauseReason() -> None:
    text = _read_skill("stop-conditions")
    # PauseReason is `Literal["stuck_task", "budget_cap_exceeded", "loop_pattern"]`.
    # Extract the literal members at runtime.
    from typing import get_args

    reasons = get_args(PauseReason)
    assert reasons  # sanity
    for reason in reasons:
        assert reason in text, f"stop-conditions/SKILL.md is missing watchdog reason {reason!r}"


def test_stop_conditions_skill_mentions_agent_stop_audit_event() -> None:
    # Watchdog's `detect_stuck_tasks` reads audit rows of type
    # `agent_stop` or `action_executed` to decide a task isn't stuck. If
    # the skill drops `agent_stop`, agents will stop emitting it, and the
    # watchdog will start firing false positives.
    text = _read_skill("stop-conditions")
    assert "agent_stop" in text
    assert "action_executed" in text


def test_proposal_schema_skill_documents_other_action_type() -> None:
    # Default behaviour: an action_type not on either list
    # defaults to requires_approval=true. The skill must document this so
    # agents don't assume "other" is rejected.
    text = _read_skill("proposal-schema")
    assert '"other"' in text or "`other`" in text or "Default-gated" in text


def test_proposal_schema_skill_documents_optional_proposal_id() -> None:
    # proposal_id is optional; the server generates a UUIDv4 when absent.
    # If this skill silently goes back to claiming proposal_id is required,
    # runtime agents will start minting their own again.
    text = _read_skill("proposal-schema")
    assert "proposal_id" in text
    assert "server generates" in text.lower() or "server-generated" in text.lower()
