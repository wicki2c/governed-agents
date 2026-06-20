#!/usr/bin/env bash
# Local verification gate: secrets scan, hygiene, lint, format, tests, and
# an orchestrator smoke. Run before every commit / PR.
set -euo pipefail

echo "== verify: repo hygiene =="
if ! git rev-parse --is-inside-work-tree >/dev/null 2>&1; then
  echo "WARN: not a git repo yet. Run: git init"
fi

echo "== verify: secrets scan =="
# Fast-path scan for obviously-shaped secrets. gitleaks (in CI / pre-commit)
# is the load-bearing scanner; this is the cheap local guard. The sk- tail is
# >=20 chars so it matches real keys, not ordinary hyphenated English.
if grep -RIn --exclude-dir=node_modules --exclude-dir=.git --exclude-dir=.venv \
  -E "(sk-[A-Za-z0-9]{20,}|sk_live_[A-Za-z0-9]{10,}|ghp_[A-Za-z0-9]{20,}|AKIA[0-9A-Z]{16}|-----BEGIN (RSA|OPENSSH) PRIVATE KEY-----)" \
  . >/dev/null 2>&1; then
  echo "FAIL: potential secret pattern found in repo."
  exit 1
fi

echo "== verify: .env files not committed =="
if git ls-files | grep -E "^\.env(\.|$)" | grep -v "\.example$" >/dev/null 2>&1; then
  echo "FAIL: .env file(s) tracked by git. They must be gitignored."
  exit 1
fi

echo "== verify: required files exist =="
required=(
  "README.md"
  "LICENSE"
  "pyproject.toml"
  "infra/app.py"
  "infra/db.py"
  "infra/watchdog.py"
  "infra/proposal_policy.toml"
  "agents/orchestrator/runner.py"
  ".claude/skills/proposal-schema/SKILL.md"
  ".claude/skills/budget-check/SKILL.md"
  ".claude/skills/stop-conditions/SKILL.md"
)
for f in "${required[@]}"; do
  if [[ ! -f "$f" ]]; then
    echo "FAIL: required file missing: $f"
    exit 1
  fi
done

echo "== verify: Python toolchain =="
if ! command -v uv >/dev/null 2>&1; then
  echo "FAIL: uv not on PATH. Install: curl -LsSf https://astral.sh/uv/install.sh | sh"
  exit 1
fi

echo "== verify: ruff lint + format =="
uv run ruff check . || { echo "FAIL: ruff lint"; exit 1; }
uv run ruff format --check . || { echo "FAIL: ruff format"; exit 1; }

echo "== verify: pytest =="
uv run pytest -q || { echo "FAIL: pytest"; exit 1; }

echo "== verify: smoke =="
./scripts/smoke.sh || { echo "FAIL: smoke"; exit 1; }

echo "PASS: verify completed."
