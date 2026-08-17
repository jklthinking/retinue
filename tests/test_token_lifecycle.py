"""An agent credential can end: expiry is enforced, revocation and rotation exist.

Before this, an issued bearer token lived until someone edited the database:
nothing expired, nothing could be rotated, and revocation meant SQL. Each test
here exercises the administrative lifecycle through the HTTP surface only.
"""

from __future__ import annotations

import datetime as dt
import sqlite3

import pytest
from fastapi.testclient import TestClient

from server.app import create_app
from server.db import (
    Actor,
    ApiToken,
    LATEST_SCHEMA_VERSION,
    User,
    make_session_factory,
    migrate_database,
    utcnow,
)
from server.security import hash_password, hash_token

LIVE_BEARER = "live-agent-bearer-for-tests"
EXPIRED_BEARER = "expired-agent-bearer-for-tests"

OLD_API_TOKENS_DDL = """
CREATE TABLE api_tokens (
    id INTEGER NOT NULL PRIMARY KEY,
    token_hash VARCHAR(128) NOT NULL UNIQUE,
    actor_id VARCHAR(64) NOT NULL,
    label VARCHAR(128) NOT NULL,
    disabled BOOLEAN NOT NULL,
    created_at DATETIME NOT NULL,
    last_used_at DATETIME
)
"""


@pytest.fixture()
def token_env(tmp_path):
    factory = make_session_factory(tmp_path / "tokens.db")
    with factory() as db:
        db.add(Actor(id="held-agent", kind="agent", display_name="Held Agent"))
        db.flush()
        db.add(
            User(
                username="root-admin",
                password_hash=hash_password("admin-pass-1234"),
                role="admin",
            )
        )
        db.add(
            ApiToken(
                token_hash=hash_token(LIVE_BEARER),
                actor_id="held-agent",
                label="live credential",
            )
        )
        db.add(
            ApiToken(
                token_hash=hash_token(EXPIRED_BEARER),
                actor_id="held-agent",
                label="expired credential",
                expires_at=utcnow() - dt.timedelta(minutes=1),
            )
        )
        db.commit()
    client = TestClient(create_app(factory))
    login = client.post(
        "/api/auth/login",
        json={"username": "root-admin", "password": "admin-pass-1234"},
    )
    assert login.status_code == 200
    return client


def bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def listed_tokens(client: TestClient) -> dict[str, dict]:
    response = client.get("/api/admin/tokens")
    assert response.status_code == 200
    return {entry["label"]: entry for entry in response.json()}


def test_expired_token_no_longer_authenticates(token_env):
    alive = token_env.get("/api/auth/me", headers=bearer(LIVE_BEARER))
    assert alive.status_code == 200
    assert alive.json()["actor_id"] == "held-agent"

    dead = token_env.get("/api/auth/me", headers=bearer(EXPIRED_BEARER))
    assert dead.status_code == 401


def test_revocation_kills_a_token_immediately(token_env):
    token_id = listed_tokens(token_env)["live credential"]["id"]

    revoked = token_env.post(f"/api/admin/tokens/{token_id}/revoke")
    assert revoked.status_code == 200
    assert revoked.json()["status"] == "revoked"

    assert token_env.get("/api/auth/me", headers=bearer(LIVE_BEARER)).status_code == 401
    assert token_env.post(f"/api/admin/tokens/{token_id}/revoke").status_code == 409
    assert token_env.post("/api/admin/tokens/999999/revoke").status_code == 404


def test_rotation_replaces_the_secret_and_keeps_the_grant(token_env):
    issued = token_env.post(
        "/api/admin/tokens",
        json={"actor_id": "held-agent", "label": "rotating", "expires_in_days": 30},
    )
    assert issued.status_code == 200
    old_plaintext = issued.json()["token"]
    assert issued.json()["expires_at"] is not None
    old_entry = listed_tokens(token_env)["rotating"]

    rotated = token_env.post(f"/api/admin/tokens/{old_entry['id']}/rotate")
    assert rotated.status_code == 200
    new_plaintext = rotated.json()["token"]
    assert new_plaintext != old_plaintext
    assert rotated.json()["revoked_id"] == old_entry["id"]

    replaced = token_env.get("/api/auth/me", headers=bearer(new_plaintext))
    assert replaced.status_code == 200
    assert replaced.json()["actor_id"] == "held-agent"
    assert (
        token_env.get("/api/auth/me", headers=bearer(old_plaintext)).status_code == 401
    )

    entries = token_env.get("/api/admin/tokens").json()
    survivors = [e for e in entries if e["label"] == "rotating" and not e["disabled"]]
    assert len(survivors) == 1
    # The rotation replaced the secret, not the grant's lifetime.
    assert survivors[0]["expires_at"] == old_entry["expires_at"]


def test_rotation_refuses_the_dead_and_the_unknown(token_env):
    token_id = listed_tokens(token_env)["live credential"]["id"]
    rotated = token_env.post(
        f"/api/admin/tokens/{token_id}/rotate", json={"expires_in_days": 1}
    )
    assert rotated.status_code == 200
    assert rotated.json()["expires_at"] is not None

    assert token_env.post(f"/api/admin/tokens/{token_id}/rotate").status_code == 409
    assert token_env.post("/api/admin/tokens/999999/rotate").status_code == 404


def test_old_database_gains_the_expiry_column(tmp_path):
    db_path = tmp_path / "old-tokens.db"
    raw = sqlite3.connect(db_path)
    raw.execute(OLD_API_TOKENS_DDL)
    raw.execute(
        "INSERT INTO api_tokens VALUES (1, 'hash', 'held-agent', 'legacy', 0,"
        " '2026-01-01', NULL)"
    )
    raw.commit()
    raw.close()

    result = migrate_database(db_path)
    assert result.to_version == LATEST_SCHEMA_VERSION

    raw = sqlite3.connect(db_path)
    columns = {row[1] for row in raw.execute("PRAGMA table_info(api_tokens)")}
    row = raw.execute(
        "SELECT expires_at FROM api_tokens WHERE id = 1"
    ).fetchone()
    raw.close()
    assert "expires_at" in columns
    assert row[0] is None  # legacy credentials stay non-expiring
