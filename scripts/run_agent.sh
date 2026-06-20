#!/usr/bin/env bash
# Invoke one runtime agent for one task via the orchestrator runner.
#
# Usage:
#   ./scripts/run_agent.sh <agent-id> [--task <task>]
#
# The bundled `noop` agent is a zero-cost smoke that exercises the full
# proposal -> approve -> execute -> audit loop. Add your own agents under
# agents/<agent-id>/ with a CLAUDE.md, a tool_allowlist.toml (default-deny),
# and tasks/<task>.md.
#
# Reads the tool allowlist from agents/<agent-id>/tool_allowlist.toml,
# shells out to `claude -p`, and posts the budget charge from the parent
# runner (the agent never charges itself).

set -euo pipefail

if [[ $# -lt 1 ]]; then
  echo "usage: $0 <agent-id> [--task <task>]" >&2
  exit 2
fi

AGENT="$1"
shift || true

exec uv run python -m agents.orchestrator.runner --agent "$AGENT" "$@"
