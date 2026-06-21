# Changelog

All notable changes to this project are documented here.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).
While the project is pre-1.0 (0.x), the public API may change between minor
versions.

## [Unreleased]

_Nothing yet._

## [0.1.0] — 2026-06-21

The initial public release of the governance harness: the five core
primitives plus the CI, dependency, and documentation hardening landed since.

### Added

- **Proposal-gate** — every external-facing or irreversible action is a
  proposal a human approves; `never_auto_approve` actions cannot be
  self-approved (the server rejects and audit-flags the attempt).
- **Per-agent budget ledger** — token and dollar caps recorded by the runner,
  not the agent; over-cap agents are auto-paused.
- **Independent watchdog** — a separate process that pauses stuck,
  over-budget, or looping agents without the agent's cooperation.
- **Audit log** — every proposal, decision, charge, and run recorded in
  SQLite and mirrored to append-only JSONL.
- **Default-deny tool allowlists** — explicit per-agent allowlists; the runner
  composes the `claude -p` permission set from them.
- **Flat multi-agent orchestrator** — runs one agent per task under all of the
  above, with a single-page localhost dashboard for approvals and live state,
  and a credentials proxy that issues one-shot scoped tokens so agents never
  hold raw credentials.
- **`noop` smoke agent** — exercises the full
  proposal → approve → execute → audit loop with no model required.
- **Decision-hook dispatch** — register a handler to run a side effect when a
  proposal is approved (`decision_hooks.register`).
- **Worked examples** — `docs/USAGE.md` with three end-to-end walkthroughs;
  an example `publisher` agent with a custom decision-hook.
- **Project docs** — `CONTRIBUTING.md` (DCO sign-off, good-first-issue
  on-ramp), issue and pull-request templates, and `CODEOWNERS` documenting the
  governance-core ownership.

### Changed

- CI runners moved to the Node 24 action runtime.
- CI cancels superseded runs via a concurrency group.
- Coverage reporting added with an 83% threshold gate.
- Dependabot enabled for GitHub Actions and uv dependencies.
- Dependency bumps: structlog 26.x, uvicorn ≥ 0.49.

### Security

- pip-audit dependency scan added to CI; the flagged pytest CVE remediated.

### Fixed

- Test suite runs warning-clean on the httpx 2 TestClient backend.

[Unreleased]: https://github.com/wicki2c/governed-agents/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/wicki2c/governed-agents/releases/tag/v0.1.0
