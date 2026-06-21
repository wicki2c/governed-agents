"""governed-agents — the CLI front-end for the governance harness.

This package bundles the `governed-agents` command. It is a thin wrapper
over the existing `infra.*` and `agents.*` modules: every subcommand routes
to the underlying entrypoint rather than reimplementing it, so the CLI never
becomes a second source of truth for orchestrator/watchdog/runner behaviour.
"""

from __future__ import annotations

__version__ = "0.1.0"

__all__ = ["__version__"]
