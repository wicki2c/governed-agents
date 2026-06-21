# Contributing to Governed Agents

Thanks for your interest! This is an early project and contributions are
welcome — bug reports, docs, tests, and focused features alike.

## Good first issues

New here? Start with an issue labeled
[`good first issue`](https://github.com/wicki2c/governed-agents/issues?q=is%3Aopen+is%3Aissue+label%3A%22good+first+issue%22)
— these are scoped, self-contained, and a maintainer has already sketched the
approach. [`help wanted`](https://github.com/wicki2c/governed-agents/issues?q=is%3Aopen+is%3Aissue+label%3A%22help+wanted%22)
issues are a good next step. Comment on an issue to claim it before opening a
PR, so two people don't pick up the same one.

## Ground rules

- **No secrets, ever.** Not in code, tests, fixtures, commits, or issues.
  Real credentials live in `.env.local` (gitignored) only.
- **The governance layer is the product.** Changes to the proposal-gate,
  budget ledger, or watchdog need tests. An agent must never be able to
  self-approve a `never_auto_approve` action or under-report its own spend.
- **Evidence, not summary.** PRs that change behavior should show the
  behavior — test output, a curl transcript, a dashboard screenshot.

## Developer Certificate of Origin (DCO)

We use the [DCO](https://developercertificate.org/) instead of a CLA. Sign
off every commit:

```bash
git commit -s -m "your message"
```

The `-s` adds a `Signed-off-by:` line certifying you wrote the patch (or
have the right to submit it) under the project's license.

## Getting set up

```bash
uv sync
./scripts/verify.sh    # secrets scan + ruff lint/format + pytest + smoke
```

Run `uv run ruff format .` before committing; `verify.sh` checks formatting.

## Pull requests

1. Branch from `main`.
2. Keep the change focused — one concern per PR.
3. Add or update tests. `uv run pytest -q` must be green.
4. Update docs in the same PR when behavior changes — and add a line to
   `CHANGELOG.md` under `## [Unreleased]` for any user-visible change.
5. Sign off your commits (DCO).

## Scope

This repo is the **open-source governance core** — lane-agnostic and
secret-free. Application-specific agents, hooks, and integrations belong in
your own repo, not here. Examples that teach the framework are welcome.
