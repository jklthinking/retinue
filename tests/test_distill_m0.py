"""Distillation pipeline M0: candidates, cooling gate, privileged promotion."""

from __future__ import annotations

import datetime as dt
import sqlite3
from pathlib import Path

from fastapi.testclient import TestClient
from sqlalchemy import select

from server.app import create_app
from server.db import (
    LATEST_SCHEMA_VERSION,
    Actor,
    ApiToken,
    DistillEvent,
    KnowledgeSource,
    RuntimeSession,
    SchemaVersion,
    User,
    make_session_factory,
    migrate_database,
)
from server.security import hash_password, hash_token


FIXED_NOW = dt.datetime(2026, 8, 13, 12, 0, tzinfo=dt.timezone.utc)
ADMIN_PASS = "admin-pass-1-ok"
MEMBER_PASS = "member-pass-1-ok"
AGENT_BEARER = "distill-agent-token-1"


def _board(tmp_path: Path, monkeypatch) -> TestClient:
    monkeypatch.setattr("server.db.utcnow", lambda: FIXED_NOW)
    monkeypatch.setattr("server.distill.utcnow", lambda: FIXED_NOW)
    factory = make_session_factory(tmp_path / "distill.db")
    with factory() as db:
        db.add_all(
            [
                Actor(id="admin-op", kind="human", display_name="Admin"),
                Actor(id="alice", kind="human", display_name="Alice"),
                Actor(id="distiller", kind="agent", display_name="Distiller"),
            ]
        )
        db.flush()
        db.add_all(
            [
                User(
                    username="admin",
                    password_hash=hash_password(ADMIN_PASS),
                    role="admin",
                    actor_id="admin-op",
                ),
                User(
                    username="alice",
                    password_hash=hash_password(MEMBER_PASS),
                    role="member",
                    actor_id="alice",
                ),
            ]
        )
        db.add(
            ApiToken(
                token_hash=hash_token(AGENT_BEARER),
                actor_id="distiller",
                label="distiller",
            )
        )
        session = RuntimeSession(
            actor_id="distiller",
            runtime="codex",
            external_id="sess-distill-1",
            node="node-a",
            title="Original session title",
            summary="Original session summary",
            privacy="metadata",
            cursor=3,
            content_hash="abc123hash",
            message_count=3,
            messages_json='[{"role":"user","text":"secret body must stay"}]',
        )
        db.add(session)
        db.commit()
    return TestClient(create_app(factory))


def _login(client: TestClient, username: str, password: str) -> None:
    client.post("/api/auth/logout")
    response = client.post(
        "/api/auth/login", json={"username": username, "password": password}
    )
    assert response.status_code == 200, response.text


def _agent_headers() -> dict[str, str]:
    return {"Authorization": f"Bearer {AGENT_BEARER}"}


def _source_session_snapshot(db_path: Path, session_id: int) -> tuple:
    conn = sqlite3.connect(db_path)
    try:
        return conn.execute(
            "SELECT title, summary, content_hash, message_count, messages_json, "
            "cursor, privacy FROM runtime_sessions WHERE id = ?",
            (session_id,),
        ).fetchone()
    finally:
        conn.close()


def test_register_and_list_candidates(tmp_path, monkeypatch):
    client = _board(tmp_path, monkeypatch)
    created = client.post(
        "/api/distill/candidates",
        headers=_agent_headers(),
        json={
            "summary": "Team agreed to keep weekly retros short.",
            "source_session_id": 1,
            "origin_ref": "runtime:codex#sess-distill-1",
        },
    )
    assert created.status_code == 200, created.text
    body = created.json()
    assert body["status"] == "pending"
    assert body["summary"] == "Team agreed to keep weekly retros short."
    assert body["source_session_id"] == 1
    assert body["cooldown_until"] == "2026-08-14T12:00:00Z"
    assert body["events"][0]["event_type"] == "registered"
    assert "secret body" not in created.text

    listed = client.get(
        "/api/distill/candidates",
        headers=_agent_headers(),
        params={"status": "pending"},
    )
    assert listed.status_code == 200
    assert len(listed.json()) == 1
    assert listed.json()[0]["id"] == body["id"]


def test_promote_blocked_during_cooldown(tmp_path, monkeypatch):
    client = _board(tmp_path, monkeypatch)
    created = client.post(
        "/api/distill/candidates",
        headers=_agent_headers(),
        json={"summary": "Still cooling.", "source_session_id": 1},
    )
    assert created.status_code == 200, created.text
    candidate_id = created.json()["id"]

    _login(client, "admin", ADMIN_PASS)
    blocked = client.post(f"/api/distill/candidates/{candidate_id}/promote")
    assert blocked.status_code == 409, blocked.text
    assert "cooldown" in blocked.json()["detail"].lower()


def test_promote_after_cooldown_creates_knowledge_and_audit(tmp_path, monkeypatch):
    client = _board(tmp_path, monkeypatch)
    db_path = tmp_path / "distill.db"
    before = _source_session_snapshot(db_path, 1)

    created = client.post(
        "/api/distill/candidates",
        headers=_agent_headers(),
        json={
            "summary": "Promote me after cooling.",
            "source_session_id": 1,
            "origin_ref": "anchor:1",
            "cooldown_hours": 0,
        },
    )
    assert created.status_code == 200, created.text
    candidate_id = created.json()["id"]

    _login(client, "alice", MEMBER_PASS)
    promoted = client.post(f"/api/distill/candidates/{candidate_id}/promote")
    assert promoted.status_code == 200, promoted.text
    body = promoted.json()
    assert body["status"] == "promoted"
    assert body["promoted_entry_id"] is not None
    assert body["decided_by"] == "alice"
    assert any(event["event_type"] == "promoted" for event in body["events"])

    factory = make_session_factory(db_path)
    with factory() as db:
        entry = db.get(KnowledgeSource, body["promoted_entry_id"])
        assert entry is not None
        assert entry.kind == "distill"
        assert entry.notes == "Promote me after cooling."
        assert entry.location == "anchor:1"
        assert entry.name == f"distill-{candidate_id}"
        events = list(
            db.execute(
                select(DistillEvent).where(DistillEvent.candidate_id == candidate_id)
            ).scalars()
        )
        assert [event.event_type for event in events] == ["registered", "promoted"]

    assert _source_session_snapshot(db_path, 1) == before


def test_reject_path_leaves_audit_and_spares_session(tmp_path, monkeypatch):
    client = _board(tmp_path, monkeypatch)
    db_path = tmp_path / "distill.db"
    before = _source_session_snapshot(db_path, 1)

    created = client.post(
        "/api/distill/candidates",
        headers=_agent_headers(),
        json={"summary": "Reject this one.", "source_session_id": 1},
    )
    candidate_id = created.json()["id"]

    _login(client, "admin", ADMIN_PASS)
    rejected = client.post(
        f"/api/distill/candidates/{candidate_id}/reject",
        json={"decision_note": "too vague for org memory"},
    )
    assert rejected.status_code == 200, rejected.text
    body = rejected.json()
    assert body["status"] == "rejected"
    assert body["decision_note"] == "too vague for org memory"
    assert body["promoted_entry_id"] is None
    assert body["events"][-1]["event_type"] == "rejected"
    assert body["events"][-1]["reason"] == "too vague for org memory"

    factory = make_session_factory(db_path)
    with factory() as db:
        assert list(db.execute(select(KnowledgeSource)).scalars()) == []

    assert _source_session_snapshot(db_path, 1) == before


def test_agent_cannot_promote(tmp_path, monkeypatch):
    client = _board(tmp_path, monkeypatch)
    created = client.post(
        "/api/distill/candidates",
        headers=_agent_headers(),
        json={"summary": "agent promote denied", "cooldown_hours": 0},
    )
    candidate_id = created.json()["id"]
    denied = client.post(
        f"/api/distill/candidates/{candidate_id}/promote",
        headers=_agent_headers(),
    )
    assert denied.status_code == 403


def test_migrate_v18_to_v19(tmp_path):
    """A database stamped at v18 gains distill tables when migrated through v19 (latest is 20)."""
    assert LATEST_SCHEMA_VERSION == 20
    db_path = tmp_path / "from-v18.db"
    factory = make_session_factory(db_path)
    with factory() as db:
        assert (
            db.execute(
                select(SchemaVersion.version).where(SchemaVersion.id == 1)
            ).scalar_one()
            == 20
        )
        # Downgrade the stamp to simulate a v18 deployment that lacks the
        # distill tables only in the version bookkeeping sense; create_all on
        # migrate recreates missing tables, then stamps latest (20, via v19 distill then v20 notifier).
        db.execute(
            SchemaVersion.__table__.update()
            .where(SchemaVersion.id == 1)
            .values(version=18)
        )
        db.commit()

    conn = sqlite3.connect(db_path)
    try:
        conn.execute("DROP TABLE IF EXISTS distill_events")
        conn.execute("DROP TABLE IF EXISTS distill_candidates")
        conn.commit()
        tables = {
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
        assert "distill_candidates" not in tables
        assert conn.execute(
            "SELECT version FROM schema_version WHERE id = 1"
        ).fetchone() == (18,)
    finally:
        conn.close()

    result = migrate_database(db_path)
    assert (result.from_version, result.to_version) == (18, 20)

    conn = sqlite3.connect(db_path)
    try:
        tables = {
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
        assert "distill_candidates" in tables
        assert "distill_events" in tables
        cols = {
            row[1]
            for row in conn.execute("PRAGMA table_info(distill_candidates)")
        }
        assert {
            "id",
            "summary",
            "status",
            "cooldown_until",
            "promoted_entry_id",
            "source_session_id",
        } <= cols
        assert conn.execute(
            "SELECT version FROM schema_version WHERE id = 1"
        ).fetchone() == (20,)
    finally:
        conn.close()

    # Smoke: register works on the upgraded database.
    with factory() as db:
        db.add(Actor(id="upgrader", kind="agent", display_name="Upgrader"))
        db.add(
            ApiToken(
                token_hash=hash_token("upgrade-token"),
                actor_id="upgrader",
                label="up",
            )
        )
        db.commit()
    client = TestClient(create_app(factory))
    response = client.post(
        "/api/distill/candidates",
        headers={"Authorization": "Bearer upgrade-token"},
        json={"summary": "post-migration candidate", "cooldown_hours": 1},
    )
    assert response.status_code == 200, response.text
    assert response.json()["status"] == "pending"
