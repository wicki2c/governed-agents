# Skill: memory

## Purpose
Maintain durable, per-lane learning across runtime-agent invocations.
The orchestrator's database is shared state; this skill is about the
agent's own scratchpad-with-history.

## Location
Each runtime agent has `agents/<lane>/memory.md`. This file is committed
(unlike `workspace/` which is gitignored) so that learnings survive across
sessions and machines.

## Rules
- Never store secrets, credentials, or PII.
- Project-relevant learnings: append here.
- Reusable patterns that would help other agents: surface them as a new
  shared skill.
- Mistakes: ALWAYS record. The point of memory is to not repeat them.

## Format
```markdown
# <lane> agent memory

## What this agent has learned
- (dated entries, newest at top)

## What this agent failed at
- (dated entries with the specific failure mode)

## Patterns that worked
- (dated entries with evidence)
```

## Anti-patterns
- Don't dump full session logs here. Logs go in `infra/audit.jsonl`.
- Don't store inferred operator preferences; those belong in the project's
  CLAUDE.md, not here.
