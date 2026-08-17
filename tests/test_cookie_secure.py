"""The session cookie carries Secure exactly when the deployment can honour it.

A hard-coded Secure attribute would silently break login on the plain-HTTP
internal deployment; omitting it forever would send session tokens over clear
text once TLS arrives. The attribute therefore follows how the request
actually arrived, with an explicit operator override for both directions.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from server.app import create_app
from server.db import User, make_session_factory
from server.security import hash_password


@pytest.fixture()
def app_env(tmp_path):
    factory = make_session_factory(tmp_path / "cookies.db")
    with factory() as db:
        db.add(
            User(
                username="operator",
                password_hash=hash_password("operator-pass-123"),
                role="member",
            )
        )
        db.commit()
    return create_app(factory)


def login_cookie(client: TestClient) -> str:
    response = client.post(
        "/api/auth/login",
        json={"username": "operator", "password": "operator-pass-123"},
    )
    assert response.status_code == 200
    return response.headers["set-cookie"]


def test_plain_http_login_keeps_a_working_cookie(app_env):
    assert "secure" not in login_cookie(TestClient(app_env)).lower()


def test_https_login_marks_the_cookie_secure(app_env):
    client = TestClient(app_env, base_url="https://retinue.test")
    assert "secure" in login_cookie(client).lower()


def test_proxy_terminated_tls_is_recognised(app_env):
    client = TestClient(app_env, headers={"x-forwarded-proto": "https"})
    assert "secure" in login_cookie(client).lower()


def test_operator_override_wins_in_both_directions(app_env, monkeypatch):
    monkeypatch.setenv("RETINUE_COOKIE_SECURE", "1")
    assert "secure" in login_cookie(TestClient(app_env)).lower()

    monkeypatch.setenv("RETINUE_COOKIE_SECURE", "0")
    client = TestClient(app_env, base_url="https://retinue.test")
    assert "secure" not in login_cookie(client).lower()
