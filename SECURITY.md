# Security Policy

## Reporting a vulnerability

Please report security issues **privately** using GitHub's
["Report a vulnerability"](https://docs.github.com/en/code-security/security-advisories/guidance-on-reporting-and-writing-information-about-vulnerabilities/privately-reporting-a-security-vulnerability)
button under the repository's **Security** tab. Do **not** open a public
issue for a vulnerability.

We aim to acknowledge reports within a few business days. This is an early
project maintained on a best-effort basis.

## Important: the dashboard has no authentication

The orchestration server binds to `127.0.0.1` and has **no authentication** —
localhost access *is* the authorization model. This is by design for a
single-operator local tool.

**Do not expose the orchestrator port to a network**, put it behind a
reverse proxy without auth, or bind it to `0.0.0.0`. Anyone who can reach
the port can approve proposals and pause agents. If you need multi-user or
networked access, add an authenticating layer in front of it — that is out
of scope for the core.

## Scope

In scope: the orchestration server, watchdog, runner, and the governance
guarantees (proposal-gate enforcement, budget caps, audit integrity,
credential-proxy single-use semantics).

Out of scope: the security of agents, hooks, or integrations you write on
top of the framework, and any deployment that ignores the localhost-only
guidance above.
