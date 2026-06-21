<!--
Open the PR body with a one-line risk verdict (house style), e.g.:
  RISK: low — docs-only, no protected path.
  RISK: medium — touches the budget ledger; covered by new tests.
  RISK: high — changes the proposal-gate decision path.
Replace the line below.
-->
RISK: <low|medium|high> — <one sentence on the blast radius>

## What

<!-- What does this change, and why? Link the issue, e.g. "Closes #123". -->

## Verification

<!-- Evidence, not summary: test output, a curl transcript, or a screenshot.
     CONTRIBUTING.md asks that behavior changes show the behavior. -->

## Checklist

- [ ] Tests added/updated; `uv run pytest -q` is green
- [ ] `uv run ruff format .` run; `ruff check .` clean
- [ ] Docs updated in the same PR (if behavior changed)
- [ ] Commits signed off (DCO — `git commit -s`)
- [ ] No secrets in code, tests, fixtures, or this PR
