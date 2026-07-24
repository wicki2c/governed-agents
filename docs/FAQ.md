# FAQ

Short answers to the questions that come up most. If yours isn't here,
[open an issue](https://github.com/wicki2c/governed-agents/issues).

- [Why Python 3.12+?](#why-python-312)
- [Do I need Claude or a `claude` binary?](#do-i-need-claude-or-a-claude-binary)
- [Why is the dashboard localhost-only, with no auth?](#why-is-the-dashboard-localhost-only-with-no-auth)
- [Is this a sandbox — a security boundary against a hostile agent?](#is-this-a-sandbox--a-security-boundary-against-a-hostile-agent)
- [Can an agent lie about its own spend?](#can-an-agent-lie-about-its-own-spend)

## Why Python 3.12+?

Support surface, not syntax.

Nothing in 0.1.0 depends on a 3.12-only language feature. The floor is a
deliberate support decision for a young trust component:

1. **The harness runs beside your code, not inside it.** The orchestrator and
   watchdog are their own processes and speak HTTP; agents are wrapped by the
   runner. Your application keeps whatever Python it already uses — the floor
   only constrains the harness's own environment, and
   `uv python install 3.12` satisfies it in seconds on any machine.
2. **Claim what you verify.** CI exercises the suite on 3.12 and the package
   declares `>=3.12,<3.15`. A governance layer should promise exactly the
   platforms it tests. Every extra minor version multiplies the CI matrix and
   the back-compat surface a solo-maintained 0.x can quietly get wrong.
3. **Floors only move safely in one direction.** Widening to an older Python
   later is painless and breaks no one; walking back support that people
   already depend on does. Starting narrow is the reversible choice.
4. **3.12 is boring by 2026.** Released October 2023, default on
   Ubuntu 24.04 LTS. This tool targets operators standing up new agent
   infrastructure, not legacy app servers.

What it looks like when you hit the floor: on Python ≤3.11, pip reports
*"no matching distribution found"* — that's the version gate, not a missing
package.

## Do I need Claude or a `claude` binary?

Only for `governed-agents run`, whose bundled runner wraps a headless
`claude -p` session. Everything else — `demo`, `serve`, `watchdog`, the
dashboard, and the whole HTTP API — is model-free. Any process that can
`POST /proposals` can be governed; a pluggable runner interface for
non-Claude agents is on the roadmap.

## Why is the dashboard localhost-only, with no auth?

Deliberate, and enforced in code (ADR-0004): `serve` always binds
`127.0.0.1`, there is no `--host` flag, and the host is never read from the
environment. At laptop scale the loopback interface *is* the access control;
bolting on auth would invite the misreading that network exposure is fine.
If you need multi-user, networked approvals, you've outgrown the intended
scope — see [SECURITY.md](../SECURITY.md) and the README's
[Who this is for](../README.md#who-this-is-for).

## Is this a sandbox — a security boundary against a hostile agent?

No, and it doesn't claim to be. The design removes the agent from the paths
that matter — the gate and ledger live in a separate server process, agents
hold one-shot scoped tokens instead of raw credentials, and tools are
default-deny — so a confused or misbehaving agent can't approve itself,
un-record its spend, or quietly reach tools it wasn't given. But an agent
granted broad local execution could still attack its own host; that threat
model needs OS-level isolation (containers, VMs) underneath this harness.
The harness's job is to make well-intentioned-but-wrong autonomy safe to
operate — and misbehavior visible, attributable, and auditable.

## Can an agent lie about its own spend?

It isn't in the reporting path. The runner that wraps the agent posts budget
charges from its own accounting, an agent that stays silent still gets
charged, and the ledger plus audit trail are append-only (SQLite mirrored to
JSONL). Over-cap agents are paused by the server and the independent
watchdog — neither needs the agent's cooperation.
