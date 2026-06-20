"""Typed dispatch table for post-approval decision hooks.

When a proposal is approved, the orchestrator may need to run a side
effect for it — publish a file, call an external API, deploy a service.
Rather than branch on `action_type` inside `decide_proposal`, each side
effect is a *hook plugin*: a module that registers a handler for one or
more action_types via `register(action_type, hook)`. Importing the hook
module triggers registration as a side effect; `decide_proposal` calls
`dispatch(proposal, session)` once after the decision commits.

The core framework ships with NO hooks registered — `dispatch` returns
`None` for every action_type until a deployment adds one (the common
case: most action_types have no post-approval side effect). See the docs
for a worked example of writing and registering a hook.

**Lazy resolution.** The registry stores `(module_name, attr_name)`
tuples and re-resolves via `importlib.import_module + getattr` on every
dispatch. This is deliberate: it keeps "the hook function on the module
is the source of truth," so monkeypatching the module attribute in a
test is picked up by the next dispatch. The cost is one `getattr` per
dispatch — negligible against the surrounding HTTP + SQL work, and
dispatch fires at most once per proposal decision.

Exception isolation stays in `decide_proposal`: this module does NOT
wrap hook calls in try/except. A hook that raises propagates up; the
caller writes `audit_flag_decision_hook_error` and the proposal stays
approved — a hook failure must never poison the decision.

Hook signature contract: `Callable[[Proposal, Session], dict[str, Any]]`.
The returned dict is informational only — currently unused by callers,
preserved as a hand-off point for a future per-decision summary view.
"""

from __future__ import annotations

import importlib
from collections.abc import Callable
from typing import Any, Protocol

from sqlmodel import Session

from infra.db import Proposal


class DecisionHook(Protocol):
    def __call__(self, proposal: Proposal, session: Session) -> dict[str, Any]: ...


# Each entry is a (module_name, attr_name) pair resolved lazily on
# every dispatch — see module docstring for the rationale.
HOOK_REGISTRY: dict[str, tuple[str, str]] = {}


def register(action_type: str, hook: Callable[..., Any]) -> None:
    """Register a hook for a single action_type. Raises if a hook is
    already registered for that action_type — silent overwrite would
    make the dispatch table position-dependent on import order and is
    the exact bug class this refactor is supposed to prevent.

    `hook` must be a function with `__module__` and `__name__` set so
    the registry can re-resolve it lazily on dispatch. Lambdas declared
    at module top-level satisfy this; closures do not (their __name__
    is `<lambda>` which can't be `getattr`'d). If a test needs to
    register a closure, use `register_callable()` below."""
    if not action_type or not isinstance(action_type, str):
        raise ValueError(f"action_type must be a non-empty string; got {action_type!r}")
    if action_type in HOOK_REGISTRY:
        raise ValueError(
            f"duplicate decision-hook registration for action_type "
            f"{action_type!r}; existing={HOOK_REGISTRY[action_type]!r}, "
            f"new={hook!r}"
        )
    module_name = getattr(hook, "__module__", None)
    attr_name = getattr(hook, "__name__", None)
    if not module_name or not attr_name:
        raise ValueError(
            f"hook {hook!r} lacks __module__/__name__; use register_callable() "
            f"for closures or lambdas"
        )
    # Verify the attribute is currently resolvable. Catches typos at
    # registration time rather than at dispatch time.
    mod = importlib.import_module(module_name)
    if not hasattr(mod, attr_name):
        raise ValueError(
            f"hook resolved to {module_name}.{attr_name} but the module "
            f"has no such attribute at registration time"
        )
    HOOK_REGISTRY[action_type] = (module_name, attr_name)


def register_callable(action_type: str, hook: DecisionHook) -> None:
    """Register a hook by direct callable reference (test ergonomics).

    Unlike `register()`, this bypasses the lazy module-attribute
    lookup — the supplied callable is invoked directly on every
    dispatch. Use for test closures and ad-hoc mocks. Production hooks
    should always go through `register()` so monkey-patching the
    module attribute remains the canonical way to swap behaviour.
    """
    if not action_type or not isinstance(action_type, str):
        raise ValueError(f"action_type must be a non-empty string; got {action_type!r}")
    if action_type in HOOK_REGISTRY:
        raise ValueError(f"duplicate decision-hook registration for action_type {action_type!r}")
    _DIRECT_CALLABLES[action_type] = hook
    # Sentinel entry in HOOK_REGISTRY so duplicate-register guard fires
    # symmetrically across the two registration paths. The sentinel
    # tuple is never resolved — the direct-callable lookup wins first.
    HOOK_REGISTRY[action_type] = ("__direct__", action_type)


_DIRECT_CALLABLES: dict[str, DecisionHook] = {}


def unregister(action_type: str) -> None:
    """Remove a hook registration. Idempotent — unknown action_types
    are silently ignored. Test-only helper for restoring registry state
    between tests; production code does not unregister."""
    HOOK_REGISTRY.pop(action_type, None)
    _DIRECT_CALLABLES.pop(action_type, None)


def dispatch(proposal: Proposal, session: Session) -> dict[str, Any] | None:
    """Look up the hook for `proposal.action_type` and invoke it.

    Returns the hook's result dict, or `None` if no hook is registered
    for `proposal.action_type` (the common case — most action_types have
    no post-approval side effect, so they have no entry in the registry).

    Exceptions from the hook propagate. The caller (`decide_proposal`)
    wraps the call in try/except and writes
    `audit_flag_decision_hook_error` on any exception.
    """
    entry = HOOK_REGISTRY.get(proposal.action_type)
    if entry is None:
        return None
    module_name, attr_name = entry
    if module_name == "__direct__":
        return _DIRECT_CALLABLES[attr_name](proposal, session)
    mod = importlib.import_module(module_name)
    hook = getattr(mod, attr_name)
    return hook(proposal, session)


def registered_action_types() -> frozenset[str]:
    """Snapshot of currently-registered action_types. Test-only helper —
    production code dispatches via `dispatch()`, not by listing keys."""
    return frozenset(HOOK_REGISTRY.keys())


__all__ = [
    "HOOK_REGISTRY",
    "DecisionHook",
    "dispatch",
    "register",
    "register_callable",
    "registered_action_types",
    "unregister",
]
