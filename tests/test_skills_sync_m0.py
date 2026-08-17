"""Node-side skill sync M0: upsert inventory like session sync."""

from __future__ import annotations

from fastapi.testclient import TestClient

from server.app import create_app
from server.db import Actor, ApiToken, User, make_session_factory
from server.security import hash_password, hash_token


AGENT_BEARER = "skills-sync-agent-token-1"
ADMIN_PASS = "admin-pass-1-ok"


def _client(tmp_path) -> TestClient:
    factory = make_session_factory(tmp_path / "skills-sync.db")
    with factory() as db:
        db.add(Actor(id="owner", kind="human", display_name="Owner"))
        db.add(
            Actor(
                id="helper",
                kind="agent",
                display_name="Helper",
                runtime="codex",
                node="desk",
            )
        )
        db.add(
            User(
                username="owner",
                password_hash=hash_password(ADMIN_PASS),
                role="admin",
                actor_id="owner",
            )
        )
        db.add(
            ApiToken(
                token_hash=hash_token(AGENT_BEARER),
                actor_id="helper",
                label="helper",
            )
        )
        db.commit()
    return TestClient(create_app(factory, data_dir=tmp_path))


def _agent_headers() -> dict[str, str]:
    return {"Authorization": f"Bearer {AGENT_BEARER}"}


def _payload(**changes):
    body = {
        "node_id": "desk",
        "skills": [
            {
                "name": "node-review",
                "description": "Review a change",
                "category": "coding",
                "snapshot": {"origin_label": "helper-runtime", "version": "1"},
            }
        ],
    }
    body.update(changes)
    return body


def test_skills_sync_creates_then_unchanged_then_updated(tmp_path):
    client = _client(tmp_path)
    headers = _agent_headers()

    created = client.post("/api/skills/sync", json=_payload(), headers=headers)
    assert created.status_code == 200, created.text
    data = created.json()
    assert data["created"] == 1
    assert data["updated"] == 0
    assert data["unchanged"] == 0
    assert data["items"] == [{"name": "node-review", "sync_status": "created"}]

    again = client.post("/api/skills/sync", json=_payload(), headers=headers)
    assert again.status_code == 200, again.text
    data = again.json()
    assert data["created"] == 0
    assert data["updated"] == 0
    assert data["unchanged"] == 1
    assert data["items"] == [{"name": "node-review", "sync_status": "unchanged"}]

    changed = _payload()
    changed["skills"][0]["description"] = "Review a change carefully"
    updated = client.post("/api/skills/sync", json=changed, headers=headers)
    assert updated.status_code == 200, updated.text
    data = updated.json()
    assert data["created"] == 0
    assert data["updated"] == 1
    assert data["unchanged"] == 0
    assert data["items"] == [{"name": "node-review", "sync_status": "updated"}]

    listed = client.get("/api/skills", headers=headers)
    assert listed.status_code == 200
    by_name = {row["name"]: row for row in listed.json()}
    assert by_name["node-review"]["description"] == "Review a change carefully"
    assert by_name["node-review"]["source_kind"] == "runtime"


def test_skills_sync_requires_auth(tmp_path):
    client = _client(tmp_path)
    response = client.post("/api/skills/sync", json=_payload())
    assert response.status_code in {401, 403}


def test_skills_sync_does_not_delete_absent_skills(tmp_path):
    client = _client(tmp_path)
    headers = _agent_headers()
    first = client.post(
        "/api/skills/sync",
        json={
            "skills": [
                {"name": "keep-a", "description": "A", "category": "coding"},
                {"name": "keep-b", "description": "B", "category": "coding"},
            ]
        },
        headers=headers,
    )
    assert first.status_code == 200
    assert first.json()["created"] == 2

    second = client.post(
        "/api/skills/sync",
        json={"skills": [{"name": "keep-a", "description": "A", "category": "coding"}]},
        headers=headers,
    )
    assert second.status_code == 200
    assert second.json()["unchanged"] == 1

    names = {row["name"] for row in client.get("/api/skills", headers=headers).json()}
    assert {"keep-a", "keep-b"} <= names
