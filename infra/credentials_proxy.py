"""One-shot scoped-token issuer for runtime agents.

A core trust-boundary idea: runtime agents never hold raw credentials.
They request a one-shot token from this proxy for a specific scoped action
and present it back to redeem before performing the upstream call. Each
token is single-use and time-bounded.

This module is the contract — issue, redeem, replay-blocked — and is
deliberately NOT wired to any upstream provider. Mapping a scope to a real
credential (fetched from your secret manager at redeem time) is a
per-deployment concern.
"""

from __future__ import annotations

import hashlib
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Literal

from sqlmodel import Session, select

from infra.db import OneShotToken, utcnow


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


@dataclass
class RedeemResult:
    status: Literal["ok", "not_found", "already_used", "expired"]
    scope: str | None = None
    agent_id: str | None = None


def issue_token(
    session: Session,
    *,
    agent_id: str,
    scope: str,
    ttl_seconds: int = 300,
) -> tuple[str, OneShotToken]:
    """Mint a one-shot token. Returns (raw_token, persisted_row).

    The raw token is only ever returned here; the database stores the
    SHA-256 hash. Caller must commit the session.
    """
    if ttl_seconds <= 0:
        raise ValueError("ttl_seconds must be positive")
    raw = secrets.token_urlsafe(32)
    expires_at = utcnow() + timedelta(seconds=ttl_seconds)
    row = OneShotToken(
        token_hash=_hash(raw),
        agent_id=agent_id,
        scope=scope,
        expires_at=expires_at,
    )
    session.add(row)
    session.flush()
    return raw, row


def redeem_token(
    session: Session,
    raw_token: str,
    *,
    now: datetime | None = None,
) -> RedeemResult:
    """Mark the token used. Returns ok / not_found / already_used / expired.

    Caller must commit the session.
    """
    now = now or utcnow()
    token_hash = _hash(raw_token)
    row = session.exec(select(OneShotToken).where(OneShotToken.token_hash == token_hash)).first()
    if row is None:
        return RedeemResult(status="not_found")
    if row.used_at is not None:
        return RedeemResult(status="already_used", scope=row.scope, agent_id=row.agent_id)
    # Compare timezone-naive vs aware safely: token expires_at was stored
    # as a tz-aware datetime; SQLite round-trips datetimes as naive strings,
    # so normalise both sides to naive UTC before comparing.
    expires = row.expires_at.replace(tzinfo=None) if row.expires_at.tzinfo else row.expires_at
    now_naive = now.replace(tzinfo=None) if now.tzinfo else now
    if expires < now_naive:
        return RedeemResult(status="expired", scope=row.scope, agent_id=row.agent_id)
    row.used_at = now
    session.add(row)
    session.flush()
    return RedeemResult(status="ok", scope=row.scope, agent_id=row.agent_id)
