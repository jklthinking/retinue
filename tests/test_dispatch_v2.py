"""Mention triggers, calendar/alert/callback dispatch, and squad leader routing."""

from __future__ import annotations

import datetime as dt

from fastapi.testclient import TestClient

from server.app import create_app
from server.db import Actor, Skill, SkillBinding, User, make_session_factory
from server.dispatch_v2 import (
    apply_mentions,
    fire_due_schedules,
    parse_mention_tokens,
    resolve_mentions,
    route_open_squad_cards,
)
from server.engine import create_task
from server.security import hash_password


NOW = dt.datetime(2026, 8, 13, 12, 0, tzinfo=dt.timezone.utc)


def _factory(tmp_path):
    factory = make_session_factory(tmp_path / "dispatch-v2.db")
    with factory() as db:
        db.add_all(
            [
                Actor(
                    id="publisher",
                    kind="human",
                    display_name="Publisher",
                ),
                Actor(
                    id="scribe",
                    kind="agent",
                    display_name="撰稿",
                    role="writer",
                    goal="Draft release notes and copy.",
                ),
                Actor(
                    id="reviewer",
                    kind="agent",
                    display_name="Reviewer",
                    role="reviewer",
                    goal="Review a change and leave evidence.",
                ),
                Actor(
                    id="leader",
                    kind="agent",
                    display_name="Leader",
                    role="lead",
                    goal="Route work to the right member.",
                ),
                User(
                    username="publisher",
                    password_hash=hash_password("publisher-pass-123"),
                    role="admin",
                    actor_id="publisher",
                ),
            ]
        )
        write = Skill(
            name="writing",
            category="docs",
            description="Draft release notes and copy.",
            owners_json='["scribe"]',
            enabled=True,
        )
        review = Skill(
            name="code-review",
            category="quality",
            description="Review a change and leave evidence.",
            owners_json='["reviewer"]',
            enabled=True,
        )
        db.add_all([write, review])
        db.flush()
        db.add_all(
            [
                SkillBinding(
                    actor_id="scribe",
                    skill_id=write.id,
                    enabled=True,
                    created_by="publisher",
                ),
                SkillBinding(
                    actor_id="reviewer",
                    skill_id=review.id,
                    enabled=True,
                    created_by="publisher",
                ),
            ]
        )
        db.commit()
    return factory


def _client(tmp_path):
    factory = _factory(tmp_path)
    app = create_app(factory)
    return factory, TestClient(app)


def _login(client: TestClient) -> None:
    response = client.post(
        "/api/auth/login", json={"username": "publisher", "password": "publisher-pass-123"}
    )
    assert response.status_code == 200


def test_parse_mention_tokens_are_unique_and_ordered():
    assert parse_mention_tokens("ping @scribe and @scribe then @reviewer") == [
        "scribe",
        "reviewer",
    ]
    assert parse_mention_tokens("no mentions here") == []


def test_resolve_mentions_accepts_id_and_display_name(tmp_path):
    factory = _factory(tmp_path)
    with factory() as db:
        hits = resolve_mentions(db, "请 @撰稿 看一下, copy @unknown")
        assert [hit.actor_id for hit in hits] == ["scribe"]


def test_review_mention_invites_open_card_and_leaves_both_events(tmp_path):
    factory, client = _client(tmp_path)
    _login(client)
    created = client.post(
        "/api/tasks",
        json={"title": "Hall card", "open_dispatch": True, "note": "posted to hall"},
    )
    assert created.status_code == 200
    task_id = created.json()["id"]
    comment = client.post(
        f"/api/tasks/{task_id}/reviews",
        json={
            "body": "请 @scribe 接这张挂单",
            "idempotency_key": "board:comment:mention-01",
        },
    )
    assert comment.status_code == 200
    card = client.get(f"/api/tasks/{task_id}").json()
    types = [event["type"] for event in card["chain"]]
    assert "mention_trigger" in types
    assert "mention_result" in types
    result = next(event for event in card["chain"] if event["type"] == "mention_result")
    assert result["payload"]["action"] == "invited"
    assert result["payload"]["assigned"] == "scribe"
    assert card["next"] == "scribe"
    assert card["open_dispatch"] is True
    replay = client.post(
        f"/api/tasks/{task_id}/reviews",
        json={
            "body": "请 @scribe 接这张挂单",
            "idempotency_key": "board:comment:mention-01",
        },
    )
    assert replay.status_code == 200
    again = client.get(f"/api/tasks/{task_id}").json()
    assert [event["type"] for event in again["chain"]].count("mention_trigger") == 1


def test_holder_note_mention_reassigns_queued_card(tmp_path):
    factory, client = _client(tmp_path)
    _login(client)
    created = client.post(
        "/api/tasks",
        json={"title": "Assigned draft", "holder": "publisher", "note": "keep with me"},
    )
    task_id = created.json()["id"]
    updated = client.post(
        f"/api/tasks/{task_id}/update",
        json={"note": "转给 @scribe 起草"},
    )
    assert updated.status_code == 200
    card = client.get(f"/api/tasks/{task_id}").json()
    assert card["holder"] == "scribe"
    result = next(event for event in card["chain"] if event["type"] == "mention_result")
    assert result["payload"]["action"] == "reassigned"
    assert "转派" in result["did"] or "reassigned" in result["did"]


def test_doing_card_mention_notifies_without_stealing(tmp_path):
    factory = _factory(tmp_path)
    with factory() as db:
        task = create_task(
            db,
            title="In flight",
            created_by="publisher",
            holder="publisher",
        )
        from server.engine import update_task

        update_task(
            db, task, who="publisher", is_privileged=True, status="doing", note="start"
        )
        outcome = apply_mentions(
            db,
            task,
            who="publisher",
            text="请 @reviewer 看一眼进度",
            source_type="note",
            source_key="note:doing",
            is_privileged=True,
        )
        db.commit()
        assert outcome is not None
        assert outcome["action"] == "notified"
        assert task.holder == "publisher"
        types = [event.event_type for event in task.events]
        assert types[-2:] == ["mention_trigger", "mention_result"]


def test_alert_and_callback_are_idempotent_and_named_on_the_chain(tmp_path):
    _factory, client = _client(tmp_path)
    _login(client)
    first = client.post(
        "/api/dispatch/events",
        json={
            "source": "alert",
            "idempotency_key": "alert:disk-full-001",
            "title": "Disk pressure on node-a",
            "open_dispatch": True,
            "priority": "high",
            "note": "opened from alert",
        },
    )
    assert first.status_code == 200
    assert first.json()["created"] is True
    task_id = first.json()["id"]
    replay = client.post(
        "/api/dispatch/events",
        json={
            "source": "alert",
            "idempotency_key": "alert:disk-full-001",
            "title": "Disk pressure on node-a",
            "open_dispatch": True,
            "priority": "high",
            "note": "opened from alert",
        },
    )
    assert replay.status_code == 200
    assert replay.json()["created"] is False
    assert replay.json()["id"] == task_id
    clash = client.post(
        "/api/dispatch/events",
        json={
            "source": "alert",
            "idempotency_key": "alert:disk-full-001",
            "title": "Different title",
            "open_dispatch": True,
            "priority": "high",
        },
    )
    assert clash.status_code == 409
    callback = client.post(
        "/api/dispatch/events",
        json={
            "source": "callback",
            "idempotency_key": "hook:build-77",
            "title": "Build 77 failed",
            "open_dispatch": True,
        },
    )
    assert callback.status_code == 200
    card = client.get(f"/api/tasks/{task_id}").json()
    assert card["chain"][0]["payload"]["dispatch_trigger"]["source"] == "alert"


def test_due_schedule_opens_a_card_through_the_reclaim_sweep(tmp_path):
    factory, client = _client(tmp_path)
    _login(client)
    created = client.post(
        "/api/dispatch/schedules",
        json={
            "schedule_key": "nightly-notes",
            "title": "Nightly notes pack",
            "fire_at": "2020-01-01T00:00:00+00:00",
            "open_dispatch": True,
            "note": "opened by calendar",
        },
    )
    assert created.status_code == 200
    assert created.json()["status"] == "pending"
    swept = client.post("/api/tasks/reclaim")
    assert swept.status_code == 200
    assert swept.json()["fired"]
    assert swept.json()["fired"][0]["schedule_key"] == "nightly-notes"
    task_id = swept.json()["fired"][0]["task_id"]
    card = client.get(f"/api/tasks/{task_id}").json()
    assert card["open_dispatch"] is True
    assert card["title"] == "Nightly notes pack"
    assert card["chain"][0]["payload"]["dispatch_trigger"]["source"] == "schedule"
    listed = client.get("/api/dispatch/schedules").json()
    assert listed[0]["status"] == "fired"
    assert listed[0]["last_task_id"] == task_id


def test_repeating_schedule_advances_instead_of_closing(tmp_path):
    factory = _factory(tmp_path)
    with factory() as db:
        from server.dispatch_v2 import create_dispatch_schedule

        create_dispatch_schedule(
            db,
            schedule_key="hourly-scan",
            title="Hourly scan",
            fire_at=NOW - dt.timedelta(minutes=5),
            created_by="publisher",
            open_dispatch=True,
            repeat_seconds=3600,
        )
        fired = fire_due_schedules(db, now=NOW)
        db.commit()
        assert len(fired) == 1
        from server.db import DispatchSchedule
        from sqlalchemy import select

        row = db.execute(select(DispatchSchedule)).scalar_one()
        assert row.status == "pending"
        fired_at = row.fire_at
        if fired_at.tzinfo is None:
            fired_at = fired_at.replace(tzinfo=dt.timezone.utc)
        assert fired_at > NOW
        assert row.last_task_id == fired[0]["task_id"]


def test_squad_leader_route_uses_agent_match_and_writes_reason(tmp_path):
    factory, client = _client(tmp_path)
    _login(client)
    squad = client.post(
        "/api/squads",
        json={
            "id": "docs-crew",
            "display_name": "Docs crew",
            "leader_id": "leader",
            "members": ["scribe", "reviewer", "leader"],
        },
    )
    assert squad.status_code == 200
    assert "scribe" in squad.json()["members"]
    created = client.post(
        "/api/tasks",
        json={
            "title": "Draft release notes",
            "open_dispatch": True,
            "squad_id": "docs-crew",
            "acceptance": ["Write the release notes"],
            "note": "addressed to the docs crew",
        },
    )
    assert created.status_code == 200
    task_id = created.json()["id"]
    assert created.json()["squad_id"] == "docs-crew"
    assert created.json()["open_dispatch"] is True
    routed = client.post(f"/api/tasks/{task_id}/squad-route")
    assert routed.status_code == 200
    body = routed.json()
    assert body["holder"] == "scribe"
    assert body["open_dispatch"] is False
    assert body["routed"]["chosen"] == "scribe"
    assert "依据" in body["routed"]["reason"] or body["routed"]["reason"]
    chain_text = " ".join(event["did"] for event in body["chain"])
    assert "领队路由：派给 scribe" in chain_text
    assert any(event["type"] == "squad_route" for event in body["chain"])


def test_reclaim_sweep_routes_open_squad_cards(tmp_path):
    factory = _factory(tmp_path)
    with factory() as db:
        from server.dispatch_v2 import create_squad

        create_squad(
            db,
            squad_id="review-crew",
            display_name="Review crew",
            leader_id="leader",
            created_by="publisher",
            members=["reviewer", "scribe"],
        )
        task = create_task(
            db,
            title="Review a change on the board",
            created_by="publisher",
            holder="publisher",
            open_dispatch=True,
            squad_id="review-crew",
        )
        routed = route_open_squad_cards(db)
        db.commit()
        assert routed
        assert routed[0]["chosen"] == "reviewer"
        db.refresh(task)
        assert task.holder == "reviewer"
        assert task.open_dispatch is False
        assert any(
            event.event_type == "squad_route" and "派给 reviewer" in event.did
            for event in task.events
        )


def test_agent_cannot_create_a_schedule(tmp_path):
    factory, client = _client(tmp_path)
    _login(client)
    token = client.post("/api/admin/tokens", json={"actor_id": "scribe"}).json()["token"]
    denied = client.post(
        "/api/dispatch/schedules",
        json={
            "schedule_key": "agent-schedule",
            "title": "Should fail",
            "fire_at": NOW.isoformat(),
            "open_dispatch": True,
        },
        headers={"Authorization": f"Bearer {token}"},
    )
    assert denied.status_code == 403
