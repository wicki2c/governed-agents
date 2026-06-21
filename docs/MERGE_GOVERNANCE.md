# Governing merges on a single-maintainer repo

If you run a governance harness, your merge step is itself a control
surface and deserves the same scrutiny as the proposal-gate. The merge
is the moment a change becomes real in your default branch, so the rule
that decides *which* changes may merge automatically — and which must
wait for a human — is part of your governance posture, not an afterthought
left to the platform's defaults.

## The trap: CODEOWNERS doesn't gate merges on a solo repo

GitHub CODEOWNERS plus the "Require review from Code Owners" branch
protection setting is the obvious way to hold protected paths. You list
the sensitive files, mark the rule as required, and assume nothing in
that set merges without an owner's eyes on it. On a single-maintainer
repo this silently fails, because the only reviewer is also the only
author. There is no configuration of the setting that gives you what you
want:

- **No-op at 0 required approvals.** With `require_code_owner_reviews`
  turned on but the required-approvals count left at 0, an owner's PR
  satisfies the rule with zero actual review. The gate is decorative: it
  is "on," it shows green, and it checks nothing.
- **Deadlock at one or more required approvals.** Raise the count to 1 or
  more and GitHub forbids you from approving your own PR. On a solo repo
  nobody else can approve it either, so every PR that touches a protected
  path is permanently blocked. The gate is real, but it stops all work.

So on a single-maintainer repo the CODEOWNERS review gate is either
decorative or a deadlock. It cannot be the real control over what merges.

## Where the real gate belongs: your merge automation

Move the hold from *who reviews* to *what changed*. The decision you
actually care about — "did this PR touch a protected path?" — is a fact
about the diff, not about reviewer availability. So put the gate in
whatever automation marks PRs ready and merges them, where it can read
the diff and act on it.

The invariant is simple: a PR that touches a protected path must never
auto-merge. It parks as a held draft for a human to review and merge by
hand. Only PRs that touch nothing in the protected set are allowed to
proceed automatically.

## The pattern: draft → path-check → conditional merge

1. **Open every PR as a draft.** A draft PR is not mergeable, which makes
   "not yet cleared" the safe default. Nothing merges until the
   automation explicitly promotes it.
2. **Define the owned set once.** This is the same list of paths you would
   put in CODEOWNERS. Keep `.github/CODEOWNERS` as the single source of
   that list so the automation and the documentation never drift apart.
3. **Path-check the diff against base.** Compute the files the branch
   changes relative to the base branch:

   ```bash
   git diff --name-only main...<branch>
   ```

   Then intersect that list of changed files with the owned set.
4. **Branch on the result.**
   - **Empty intersection** (the PR touches non-protected paths only):
     the automation marks the PR ready for review and merges it once CI is
     green.
   - **Non-empty intersection** (the PR touches a protected path): leave
     it as a held draft. A human reviews and merges it by hand.

The orchestrator that does the marking and merging is the trust anchor
here — the same principle the harness applies elsewhere, where the server
(not the agent) owns the decision. This is the proposal-gate idea applied
to your repo's merge step: an untrusted actor proposes a change, and a
trusted controller decides whether it may take effect.

## Wiring branch protection to match

- **Configure branch protection as status-checks-only.** Require CI to be
  green, and do not rely on required code-owner reviews to gate protected
  paths. The path-check in your automation is what enforces the hold.
- **Keep CODEOWNERS in the repo anyway.** It still documents who owns what,
  and it drives the owned set your path-check reads. It simply is not the
  enforcement mechanism — it is the source of the list, not the gate.

Configured this way you avoid both the no-op and the deadlock while
keeping a real, enforced hold on every protected path.

## Why this is stronger than review-based gating

The hold is deterministic — it is path math against the diff, not a bet on
human availability — so it survives a solo maintainer and stays auditable.
That is the same posture the rest of the harness takes toward agent
actions.

## See also

- [CONTRIBUTING.md](../CONTRIBUTING.md) — PR workflow and DCO.
- [.github/CODEOWNERS](../.github/CODEOWNERS) — the owned set this pattern
  path-checks against.
- [SECURITY.md](../SECURITY.md) — related "the orchestrator is the trust
  anchor" posture.
