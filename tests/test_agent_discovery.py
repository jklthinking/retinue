from __future__ import annotations

import importlib
from pathlib import Path

from fastapi.testclient import TestClient

from server.app import create_app
from server.db import Actor, User, make_session_factory
from server.discovery import scan_local_runtimes
from server.security import hash_password


def test_local_runtime_scan_only_returns_safe_hints(tmp_path: Path):
    home = tmp_path / "home"
    (home / ".codex" / "sessions").mkdir(parents=True)
    (home / ".claude" / "projects").mkdir(parents=True)

    rows = scan_local_runtimes(home)

    assert {row["runtime"] for row in rows} == {"codex", "claude-code"}
    codex = next(row for row in rows if row["runtime"] == "codex")
    assert codex["path_hint"] == "~/.codex/sessions"
    assert str(home) not in str(codex)
    assert "label" in codex


def _client(tmp_path: Path) -> TestClient:
    factory = make_session_factory(tmp_path / "discovery.db")
    with factory() as db:
        db.add_all(
            [
                Actor(id="owner", kind="human", display_name="负责人"),
                Actor(
                    id="scribe",
                    kind="agent",
                    display_name="撰稿智能体",
                    runtime="codex",
                ),
                User(
                    username="owner",
                    password_hash=hash_password("owner-pass-123"),
                    role="admin",
                    actor_id="owner",
                ),
            ]
        )
        db.commit()
    return TestClient(create_app(factory, data_dir=tmp_path))


def _login(client: TestClient) -> None:
    response = client.post(
        "/api/auth/login",
        json={"username": "owner", "password": "owner-pass-123"},
    )
    assert response.status_code == 200


def test_discovery_groups_metadata_and_reports_missing_binding(tmp_path: Path, monkeypatch):
    # The discovery route lives in the actors router; patch the lookup there.
    app_module = importlib.import_module("server.routers.actors")
    monkeypatch.setattr(
        app_module,
        "scan_local_runtimes",
        lambda: [
            {
                "runtime": "codex",
                "label": "Codex",
                "path_hint": "~/.codex/sessions",
                "last_changed_at": None,
            }
        ],
    )
    client = _client(tmp_path)
    _login(client)
    synced = client.post(
        "/api/sessions/sync",
        json={
            "actor_id": "scribe",
            "runtime": "codex",
            "external_id": "safe-session-1",
            "privacy": "metadata",
            "cursor": 0,
            "message_count": 0,
        },
    )
    assert synced.status_code == 200

    response = client.get("/api/agent-discovery")

    assert response.status_code == 200
    body = response.json()
    assert body["privacy"] == "不读取会话正文、提示词、密钥或绝对路径"
    codex = next(item for item in body["runtimes"] if item["runtime"] == "codex")
    assert codex["source"] == "本机扫描"
    assert codex["session_count"] == 1
    assert codex["agent_ids"] == ["scribe"]
    assert body["attention"][0]["actor_id"] == "scribe"
    assert "运行节点" in body["attention"][0]["missing"]


def test_admin_can_complete_actor_runtime_binding(tmp_path: Path):
    client = _client(tmp_path)
    _login(client)

    response = client.post(
        "/api/actors/scribe/update",
        json={
            "runtime": "codex",
            "model": "gpt-5.6",
            "node": "windows",
            "display_name": "写作智能体",
            "role": "报告撰写",
            "goal": "把业务事实整理成清晰报告。",
        },
    )

    assert response.status_code == 200
    assert response.json()["node"] == "windows"
    assert response.json()["model"] == "gpt-5.6"
    assert response.json()["role"] == "报告撰写"
    assert response.json()["goal"] == "把业务事实整理成清晰报告。"
