"""Actor-skill bindings, enablement, authz, import provenance, and claim briefing."""

from __future__ import annotations

from fastapi.testclient import TestClient

from server.app import create_app
from server.db import Actor, Skill, User, make_session_factory
from server.security import hash_password
from server.skill_ops import PILOT_BINDINGS, apply_pilot_bindings


def _client(tmp_path) -> TestClient:
    factory = make_session_factory(tmp_path / "skills.db")
    with factory() as db:
        db.add(Actor(id="owner", kind="human", display_name="Owner"))
        db.add(Actor(id="member", kind="human", display_name="Member"))
        db.add(Actor(id="scribe", kind="agent", display_name="Scribe", runtime="codex"))
        db.add(Actor(id="throne-codex", kind="agent", display_name="Codex"))
        db.add(Actor(id="windows-cursor", kind="agent", display_name="Cursor"))
        db.add(
            User(
                username="owner",
                password_hash=hash_password("owner-pass-123"),
                role="admin",
                actor_id="owner",
            )
        )
        db.add(
            User(
                username="member",
                password_hash=hash_password("member-pass-123"),
                role="member",
                actor_id="member",
            )
        )
        db.add(
            Skill(
                name="review",
                category="coding",
                description="Review a change for correctness",
                enabled=True,
            )
        )
        db.add(
            Skill(
                name="plan",
                category="coding",
                description="Break work into a plan",
                enabled=True,
            )
        )
        db.add(
            Skill(
                name="github-pr-collaboration-workflow",
                category="coding",
                description="Collaborate on a pull request",
                enabled=True,
            )
        )
        db.add(
            Skill(
                name="test-driven-development",
                category="coding",
                description="Write tests first",
                enabled=True,
            )
        )
        db.add(
            Skill(
                name="systematic-debugging",
                category="coding",
                description="Debug from evidence",
                enabled=True,
            )
        )
        db.add(
            Skill(
                name="github-pr-workflow",
                category="coding",
                description="Open and land a pull request",
                enabled=True,
            )
        )
        db.add(
            Skill(
                name="paused-skill",
                category="coding",
                description="Catalog disabled",
                enabled=False,
            )
        )
        db.commit()
    return TestClient(create_app(factory, data_dir=tmp_path))


def _login(client: TestClient, username: str, password: str) -> None:
    response = client.post(
        "/api/auth/login", json={"username": username, "password": password}
    )
    assert response.status_code == 200


def _agent_headers(client: TestClient) -> dict[str, str]:
    _login(client, "owner", "owner-pass-123")
    response = client.post("/api/admin/tokens", json={"actor_id": "scribe"})
    assert response.status_code == 200
    return {"Authorization": f"Bearer {response.json()['token']}"}


def test_bind_enable_disable_unbind_and_event_chain(tmp_path):
    client = _client(tmp_path)
    _login(client, "owner", "owner-pass-123")

    bound = client.post(
        "/api/actors/scribe/skills",
        json={"name": "review", "enabled": True},
    )
    assert bound.status_code == 200
    body = bound.json()
    assert body["name"] == "review"
    assert body["binding_enabled"] is True
    skill_id = body["id"]

    listed = client.get("/api/actors/scribe/skills")
    assert listed.status_code == 200
    assert [row["name"] for row in listed.json()] == ["review"]

    paused = client.post(
        f"/api/actors/scribe/skills/{skill_id}/update",
        json={"enabled": False},
    )
    assert paused.status_code == 200
    assert paused.json()["binding_enabled"] is False

    resumed = client.post(
        f"/api/actors/scribe/skills/{skill_id}/update",
        json={"enabled": True},
    )
    assert resumed.status_code == 200
    assert resumed.json()["binding_enabled"] is True

    events = client.get("/api/actors/scribe/skill-events")
    assert events.status_code == 200
    actions = [row["action"] for row in events.json()]
    assert actions == ["bind", "disable", "enable"]
    assert all(row["who"] == "owner" for row in events.json())
    assert all(row["skill_name"] == "review" for row in events.json())

    unbound = client.post(f"/api/actors/scribe/skills/{skill_id}/unbind")
    assert unbound.status_code == 200
    assert client.get("/api/actors/scribe/skills").json() == []
    assert [row["action"] for row in client.get("/api/actors/scribe/skill-events").json()][
        -1
    ] == "unbind"


def test_agent_and_member_cannot_change_bindings(tmp_path):
    client = _client(tmp_path)
    headers = _agent_headers(client)
    denied = client.post(
        "/api/actors/scribe/skills",
        json={"name": "review"},
        headers=headers,
    )
    assert denied.status_code == 403

    client.post("/api/auth/logout")
    _login(client, "member", "member-pass-123")
    denied_member = client.post(
        "/api/actors/scribe/skills",
        json={"name": "review"},
    )
    assert denied_member.status_code == 403
    assert client.get("/api/actors/scribe/skills").status_code == 200


def test_claim_briefing_omits_disabled_bindings(tmp_path):
    client = _client(tmp_path)
    _login(client, "owner", "owner-pass-123")
    review = client.post(
        "/api/actors/scribe/skills", json={"name": "review", "enabled": True}
    ).json()
    client.post("/api/actors/scribe/skills", json={"name": "plan", "enabled": True})
    client.post(
        f"/api/actors/scribe/skills/{review['id']}/update",
        json={"enabled": False},
    )

    created = client.post(
        "/api/tasks",
        json={
            "title": "Write a brief",
            "open_dispatch": True,
            "note": "posted for claim briefing",
        },
    )
    assert created.status_code == 200
    task_id = created.json()["id"]

    headers = _agent_headers(client)
    claimed = client.post(
        f"/api/tasks/{task_id}/claim",
        json={"note": "taking the card"},
        headers=headers,
    )
    assert claimed.status_code == 200
    briefing = claimed.json()["skill_briefing"]
    assert briefing["actor_id"] == "scribe"
    assert [row["name"] for row in briefing["skills"]] == ["plan"]
    mine = client.get("/api/me/skill-briefing", headers=headers)
    assert mine.status_code == 200
    assert [row["name"] for row in mine.json()["skills"]] == ["plan"]


def test_import_keeps_snapshot_importer_and_risk_notice(tmp_path):
    client = _client(tmp_path)
    headers = _agent_headers(client)
    imported = client.post(
        "/api/skills/import",
        json={
            "name": "runtime-helper",
            "description": "Imported helper",
            "category": "coding",
            "source": "local",
            "source_kind": "runtime",
            "snapshot": {
                "origin_label": "codex runtime inventory",
                "version": "1",
                "checksum": "abc123",
            },
        },
        headers=headers,
    )
    assert imported.status_code == 200
    body = imported.json()
    assert body["imported_by"] == "scribe"
    assert body["imported_at"]
    assert body["source_kind"] == "runtime"
    assert body["source_snapshot"]["origin_label"] == "codex runtime inventory"
    assert body["risk_notice"]
    assert "not sandboxed" in body["risk_notice"]

    refused = client.post(
        "/api/skills/import",
        json={
            "name": "unsafe-helper",
            "source_kind": "external",
            "snapshot": {"origin_label": "C:\\secret\\skill.md"},
        },
        headers=headers,
    )
    assert refused.status_code == 422


def test_import_snapshot_rejects_command_shaped_text(tmp_path):
    client = _client(tmp_path)
    headers = _agent_headers(client)
    refused = client.post(
        "/api/skills/import",
        json={
            "name": "cmd-helper",
            "source_kind": "external",
            "snapshot": {"origin_label": "--force-run helper"},
        },
        headers=headers,
    )
    assert refused.status_code == 422


def test_pilot_bindings_cover_both_executors(tmp_path):
    client = _client(tmp_path)
    _login(client, "owner", "owner-pass-123")
    applied = client.post("/api/skills/pilot-bindings")
    assert applied.status_code == 200
    payload = applied.json()
    assert payload["count"] == 6
    names_by_actor = {}
    for row in payload["applied"]:
        names_by_actor.setdefault(row["actor_id"], []).append(row["name"])
    assert names_by_actor["throne-codex"] == list(PILOT_BINDINGS[0][1])
    assert names_by_actor["windows-cursor"] == list(PILOT_BINDINGS[1][1])
    assert len(client.get("/api/actors/throne-codex/skills").json()) == 3
    assert len(client.get("/api/actors/windows-cursor/skills").json()) == 3


def test_apply_pilot_bindings_skips_missing_rows(tmp_path):
    factory = make_session_factory(tmp_path / "empty-pilot.db")
    with factory() as db:
        db.add(Actor(id="throne-codex", kind="agent", display_name="Codex"))
        db.add(Skill(name="review", description="only one inventory row"))
        applied = apply_pilot_bindings(db, who="owner")
        db.commit()
    assert applied == []


def test_matching_uses_enabled_bindings_over_owners(tmp_path):
    client = _client(tmp_path)
    _login(client, "owner", "owner-pass-123")
    client.post(
        "/api/skills",
        json={
            "name": "review",
            "description": "Review a change for correctness",
            "category": "coding",
            "owners": ["scribe"],
        },
    )
    # Binding on scribe switches it to the binding model, so the owner-only
    # review row no longer counts for that executor.
    client.post("/api/actors/scribe/skills", json={"name": "plan"})
    client.post("/api/actors/windows-cursor/skills", json={"name": "review"})
    ranked = client.get("/api/agent-match", params={"q": "review a change"})
    assert ranked.status_code == 200
    rows = {row["id"]: row for row in ranked.json()}
    assert "review" in rows["windows-cursor"]["matched_skills"]
    assert "review" not in rows["scribe"]["matched_skills"]
