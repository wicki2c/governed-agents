"""Tests for infra/decision_hooks.py — the post-approval dispatch table.

Covers:
- Registration via the standard `register()` path (module + attr name
  stored, lazy `getattr` lookup on every dispatch).
- Registration via `register_callable()` for closures / test mocks.
- Duplicate registration raises ValueError on both paths.
- Dispatch returns None for unregistered action_types.
- Dispatch invokes the right hook for registered action_types.
- Lazy resolution: monkeypatching the module attribute is picked up by
  the next dispatch.
- Exceptions from the hook propagate (the wrapping is in
  `decide_proposal`, not in `dispatch`).
- Unregister + re-register cycle.
- The example extension pattern a real deployment uses to wire a hook.
"""

from __future__ import annotations

import sys
from datetime import UTC, datetime

import pytest

from infra import decision_hooks
from infra.db import Proposal

# ---------------------------------------------------------------------------
# Test fixture — isolate registry mutation per test
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _isolate_registry(monkeypatch):
    """Each test starts with a snapshot of the current registry and
    restores it at teardown, so test additions/removals don't leak
    between tests. The core ships with an empty registry; a deployment's
    hooks (if any) would be snapshotted and preserved the same way.
    """
    saved_registry = dict(decision_hooks.HOOK_REGISTRY)
    saved_direct = dict(decision_hooks._DIRECT_CALLABLES)
    yield
    decision_hooks.HOOK_REGISTRY.clear()
    decision_hooks.HOOK_REGISTRY.update(saved_registry)
    decision_hooks._DIRECT_CALLABLES.clear()
    decision_hooks._DIRECT_CALLABLES.update(saved_direct)


def _proposal(action_type: str, *, proposal_id: str = "p-test") -> Proposal:
    """Construct an in-memory Proposal (not persisted) for dispatch tests."""
    return Proposal(
        proposal_id=proposal_id,
        agent_id="test",
        wave=0,
        submitted_at=datetime.now(UTC),
        action_type=action_type,
        payload="{}",
        payload_hash="x",
        expected_outcome="",
        expires_at=datetime.now(UTC),
        rationale="",
        links="[]",
        status="approved",
    )


# A module-level function so register() can resolve its module + name.
def _sample_hook(proposal, session):
    return {"called": True, "action_type": proposal.action_type}


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------


def test_register_stores_module_and_attr_name():
    decision_hooks.unregister("test_register_action")
    decision_hooks.register("test_register_action", _sample_hook)
    entry = decision_hooks.HOOK_REGISTRY["test_register_action"]
    assert entry == (_sample_hook.__module__, "_sample_hook")


def test_register_rejects_empty_action_type():
    with pytest.raises(ValueError, match="non-empty string"):
        decision_hooks.register("", _sample_hook)


def test_register_rejects_duplicate_action_type():
    decision_hooks.unregister("test_dup_action")
    decision_hooks.register("test_dup_action", _sample_hook)
    with pytest.raises(ValueError, match="duplicate decision-hook registration"):
        decision_hooks.register("test_dup_action", _sample_hook)


def test_register_rejects_unresolvable_hook():
    """A hook whose attribute name doesn't exist on the module is
    caught at registration time, not at dispatch time."""

    def closure_hook(p, s):
        return {}

    # `closure_hook` lives in this test function's locals; its __name__
    # is "closure_hook" but `getattr(module, "closure_hook")` will fail
    # because the module's top-level namespace doesn't have it.
    with pytest.raises(ValueError, match="no such attribute"):
        decision_hooks.register("test_closure_action", closure_hook)


# ---------------------------------------------------------------------------
# register_callable for closures
# ---------------------------------------------------------------------------


def test_register_callable_accepts_closure():
    decision_hooks.unregister("test_callable_action")
    calls: list[str] = []

    def closure_hook(proposal, session):
        calls.append(proposal.proposal_id)
        return {}

    decision_hooks.register_callable("test_callable_action", closure_hook)
    proposal = _proposal("test_callable_action", proposal_id="cb-1")
    result = decision_hooks.dispatch(proposal, None)
    assert result == {}
    assert calls == ["cb-1"]


def test_register_callable_rejects_duplicate():
    decision_hooks.unregister("test_dup_callable")
    decision_hooks.register_callable("test_dup_callable", lambda p, s: {})
    with pytest.raises(ValueError, match="duplicate decision-hook"):
        decision_hooks.register_callable("test_dup_callable", lambda p, s: {})


# ---------------------------------------------------------------------------
# Dispatch
# ---------------------------------------------------------------------------


def test_dispatch_returns_none_for_unregistered_action_type():
    proposal = _proposal("never_registered_action_xyz")
    assert decision_hooks.dispatch(proposal, None) is None


def test_dispatch_invokes_registered_hook():
    decision_hooks.unregister("test_invoke_action")
    decision_hooks.register("test_invoke_action", _sample_hook)
    proposal = _proposal("test_invoke_action", proposal_id="inv-1")
    result = decision_hooks.dispatch(proposal, None)
    assert result == {"called": True, "action_type": "test_invoke_action"}


def test_dispatch_lazy_resolution_picks_up_monkeypatch(monkeypatch):
    """Critical contract: monkeypatching the module attribute must be
    visible to the next dispatch. Preserves test ergonomics from before
    the dispatch-table refactor."""
    decision_hooks.unregister("test_lazy_action")
    decision_hooks.register("test_lazy_action", _sample_hook)

    calls: list[str] = []

    def replacement(proposal, session):
        calls.append(proposal.proposal_id)
        return {"called": False, "via": "monkeypatch"}

    this_module = sys.modules[__name__]
    monkeypatch.setattr(this_module, "_sample_hook", replacement)

    proposal = _proposal("test_lazy_action", proposal_id="lazy-1")
    result = decision_hooks.dispatch(proposal, None)
    assert calls == ["lazy-1"]
    assert result == {"called": False, "via": "monkeypatch"}


def test_dispatch_propagates_exceptions():
    """The wrapping is in decide_proposal, not in dispatch."""
    decision_hooks.unregister("test_raise_action")

    def boom(proposal, session):
        raise RuntimeError("boom from hook")

    decision_hooks.register_callable("test_raise_action", boom)
    proposal = _proposal("test_raise_action")
    with pytest.raises(RuntimeError, match="boom from hook"):
        decision_hooks.dispatch(proposal, None)


# ---------------------------------------------------------------------------
# unregister + re-register
# ---------------------------------------------------------------------------


def test_unregister_is_idempotent():
    decision_hooks.unregister("never_registered_yyy")  # must not raise
    decision_hooks.unregister("never_registered_yyy")


def test_unregister_then_reregister_works():
    decision_hooks.unregister("test_reregister_action")
    decision_hooks.register("test_reregister_action", _sample_hook)
    decision_hooks.unregister("test_reregister_action")
    decision_hooks.register("test_reregister_action", _sample_hook)
    proposal = _proposal("test_reregister_action")
    assert decision_hooks.dispatch(proposal, None) == {
        "called": True,
        "action_type": "test_reregister_action",
    }


# ---------------------------------------------------------------------------
# Example: registering a hook the way a real deployment would
# ---------------------------------------------------------------------------


def example_publish_hook(proposal, session):
    """Stand-in for a real post-approval side effect (e.g. publish a file)."""
    return {"published": proposal.proposal_id}


def test_example_hook_registers_and_dispatches():
    """Demonstrates the extension pattern: a module-level function is
    registered for an action_type, and dispatch resolves + invokes it.
    This is exactly how a deployment wires up `publish_content`,
    `deploy_service`, etc. The autouse fixture restores the registry
    afterwards, so this registration does not leak."""
    decision_hooks.unregister("publish_content")
    decision_hooks.register("publish_content", example_publish_hook)
    proposal = _proposal("publish_content", proposal_id="ex-1")
    assert decision_hooks.dispatch(proposal, None) == {"published": "ex-1"}
