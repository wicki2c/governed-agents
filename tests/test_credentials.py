"""One-shot credentials proxy: issue → redeem → second redeem rejected."""

from __future__ import annotations


def test_issue_and_redeem(client):
    issue = client.post(
        "/credentials/issue",
        json={"agent_id": "noop", "scope": "test_scope", "ttl_seconds": 60},
    )
    assert issue.status_code == 200, issue.text
    token = issue.json()["token"]
    assert token

    redeem = client.post("/credentials/redeem", json={"token": token})
    assert redeem.status_code == 200, redeem.text
    body = redeem.json()
    assert body["scope"] == "test_scope"
    assert body["agent_id"] == "noop"


def test_second_redemption_rejected(client):
    issue = client.post(
        "/credentials/issue",
        json={"agent_id": "noop", "scope": "test_scope", "ttl_seconds": 60},
    )
    token = issue.json()["token"]

    first = client.post("/credentials/redeem", json={"token": token})
    assert first.status_code == 200

    second = client.post("/credentials/redeem", json={"token": token})
    assert second.status_code == 401
    assert "already used" in second.json()["detail"]


def test_unknown_token_returns_401(client):
    response = client.post("/credentials/redeem", json={"token": "nope"})
    assert response.status_code == 401
    assert "unknown" in response.json()["detail"].lower()
