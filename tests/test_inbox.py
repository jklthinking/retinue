"""Queen inbox M0: four-lane aggregation and date-keyed daily digest."""

from __future__ import annotations

import datetime as dt
from pathlib import Path

import pytest
import yaml
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from server.app import create_app
from server.db import (
    Actor,
    ApiToken,
    Approval,
    ReminderDelivery,
    TodoEvent,
    TodoItem,
    User,
    make_session_factory,
)
from server.engine import (
    append_review_comment,
    append_review_reply,
    create_task,
    update_task,
)
from server.inbox import (
    collect_inbox,
    digest_anchor_id,
    digest_delivery_key,
    ensure_daily_digests,
    run_daily_digest,
)
from server.security import hash_password, hash_token

AGENT_BEARER = "agent-bearer-for-inbox-tests"
NOW = dt.datetime(2026, 8, 13, 9, 0, tzinfo=dt.timezone.utc)
NEXT_DAY = NOW + dt.timedelta(days=1)
PAST = dt.datetime(2020, 1, 1, 0, 0, tzinfo=dt.timezone.utc)


def _seed_board(db) -> dict[str, str]:
    db.add(Actor(id="owner", kind="human", display_name="Owner"))
    db.add(Actor(id="worker", kind="agent", display_name="Worker"))
    db.flush()

    # 待拍板: one pending approval; a decided one must not show up.
    gate = create_task(db, title="Queen gate card", created_by="owner", holder="worker")
    db.add(
        Approval(
            task_id=gate.id,
            stage_index=0,
            requested_by="worker",
            token_hash=hash_token("approval-link-token-for-inbox-tests"),
        )
    )
    decided = create_task(db, title="Decided gate card", created_by="owner", holder="worker")
    db.add(
        Approval(
            task_id=decided.id,
            stage_index=0,
            requested_by="worker",
            status="approved",
            token_hash=hash_token("decided-approval-token-for-inbox-tests"),
        )
    )

    # 待质检: one unanswered comment; an answered one must not show up.
    review = create_task(db, title="Review pending card", created_by="owner", holder="worker")
    append_review_comment(
        db, review, who="owner", idempotency_key="c1", body="请复核数据口径"
    )
    answered = create_task(db, title="Review answered card", created_by="owner", holder="worker")
    comment, _created = append_review_comment(
        db, answered, who="owner", idempotency_key="c2", body="这版可以了吗"
    )
    append_review_reply(
        db,
        answered,
        who="worker",
        review_id=comment.event_key,
        idempotency_key="r2",
        body="已补充证据",
        decision="accepted",
    )

    # 阻塞待解.
    blocked = create_task(db, title="Blocked card", created_by="owner", holder="worker")
    update_task(db, blocked, who="owner", is_privileged=True, status="doing", note="开始")
    update_task(
        db, blocked, who="owner", is_privileged=True,
        status="blocked", blocked_reason="等上游数据", note="卡住",
    )

    # 超期未动: overdue doing card and heartbeat-lost doing card.
    overdue = create_task(
        db, title="Overdue doing card", created_by="owner", holder="worker",
        due_at="2020-01-02",
    )
    update_task(db, overdue, who="owner", is_privileged=True, status="doing", note="开始")

    lost = create_task(db, title="Heartbeat lost card", created_by="owner", holder="worker")
    update_task(db, lost, who="owner", is_privileged=True, status="doing", note="开始")
    lost.lease_term = 1
    lost.lease_heartbeat_at = PAST
    lost.lease_expires_at = PAST

    # Healthy in-flight card: far-future due date, live lease — never stale.
    healthy = create_task(
        db, title="Healthy doing card", created_by="owner", holder="worker",
        due_at="2099-01-01",
    )
    update_task(db, healthy, who="owner", is_privileged=True, status="doing", note="开始")
    healthy.lease_term = 1
    healthy.lease_heartbeat_at = dt.datetime(2099, 1, 1, tzinfo=dt.timezone.utc)
    healthy.lease_expires_at = dt.datetime(2099, 1, 1, tzinfo=dt.timezone.utc)

    db.flush()
    return {
        "gate": gate.id,
        "review": review.id,
        "answered": answered.id,
        "blocked": blocked.id,
        "overdue": overdue.id,
        "lost": lost.id,
        "healthy": healthy.id,
    }


@pytest.fixture()
def inbox_env(tmp_path):
    factory = make_session_factory(tmp_path / "inbox.db")
    with factory() as db:
        ids = _seed_board(db)
        db.add(
            ApiToken(
                token_hash=hash_token(AGENT_BEARER),
                actor_id="worker",
                label="inbox tests",
            )
        )
        db.commit()
    return TestClient(create_app(factory, data_dir=tmp_path)), factory, ids


def _headers() -> dict[str, str]:
    return {"Authorization": f"Bearer {AGENT_BEARER}"}


def _get(client) -> dict:
    response = client.get("/api/inbox", headers=_headers())
    assert response.status_code == 200, response.text
    return response.json()


def test_inbox_requires_auth(inbox_env):
    client, _factory, _ids = inbox_env
    assert client.get("/api/inbox").status_code == 401


def test_inbox_four_lanes_with_counts(inbox_env):
    client, _factory, ids = inbox_env
    data = _get(client)

    assert data["generated_at"]
    assert data["today"]
    lanes = data["lanes"]
    assert set(lanes) == {"decisions", "reviews", "blocked", "stale"}

    decisions = lanes["decisions"]
    assert decisions["count"] == 1
    assert decisions["items"][0]["task_id"] == ids["gate"]
    assert decisions["items"][0]["task_title"] == "Queen gate card"

    reviews = lanes["reviews"]
    assert reviews["count"] == 1
    assert reviews["items"][0]["task_id"] == ids["review"]
    assert reviews["items"][0]["author"] == "owner"
    assert reviews["items"][0]["review_id"]

    blocked = lanes["blocked"]
    assert blocked["count"] == 1
    assert blocked["items"][0]["id"] == ids["blocked"]
    assert blocked["items"][0]["blocked_reason"] == "等上游数据"

    stale = lanes["stale"]
    assert stale["count"] == 2
    by_id = {item["task"]["id"]: item["reasons"] for item in stale["items"]}
    assert by_id[ids["overdue"]] == ["overdue"]
    assert by_id[ids["lost"]] == ["heartbeat_lost"]
    assert ids["healthy"] not in by_id


def _write_reminders_config(data_dir: Path, **overrides) -> None:
    raw = {"enabled": True, "default_channels": ["in_app"]}
    raw.update(overrides)
    (data_dir / "reminders.yaml").write_text(
        yaml.safe_dump(raw, sort_keys=False), encoding="utf-8"
    )


def _seed_owner(db, username: str = "queen", role: str = "member") -> User:
    user = User(username=username, password_hash=hash_password("owner-pass-1"), role=role)
    db.add(user)
    db.flush()
    return user


def test_digest_registration_is_date_keyed(tmp_path):
    factory = make_session_factory(tmp_path / "digest.db")
    _write_reminders_config(tmp_path)
    with factory() as db:
        user = _seed_owner(db)
        db.commit()

        first = ensure_daily_digests(db, now=NOW, data_dir=tmp_path)
        assert first["enabled"] is True
        assert first["owners"] == 1
        assert first["registered"] == 1
        assert first["date"] == "2026-08-13"
        db.commit()

        rows = list(db.execute(select(ReminderDelivery)).scalars())
        assert len(rows) == 1
        assert rows[0].delivery_key == digest_delivery_key(user.id, "2026-08-13")
        assert rows[0].channel == "in_app"
        assert rows[0].status == "pending"

        anchor = db.get(TodoItem, digest_anchor_id(user.id))
        assert anchor is not None
        assert "2026-08-13" in anchor.title

        # Same-day rescan registers nothing new.
        again = ensure_daily_digests(db, now=NOW + dt.timedelta(hours=3), data_dir=tmp_path)
        assert again["registered"] == 0
        assert db.execute(select(func.count(ReminderDelivery.id))).scalar() == 1
        db.commit()

        # Next day is a new slot keyed by the new date.
        tomorrow = ensure_daily_digests(db, now=NEXT_DAY, data_dir=tmp_path)
        assert tomorrow["registered"] == 1
        keys = {
            row.delivery_key
            for row in db.execute(select(ReminderDelivery)).scalars()
        }
        assert keys == {
            digest_delivery_key(user.id, "2026-08-13"),
            digest_delivery_key(user.id, "2026-08-14"),
        }
        db.commit()


def test_digest_disabled_config_stays_inert(tmp_path):
    factory = make_session_factory(tmp_path / "digest-off.db")
    with factory() as db:
        _seed_owner(db)
        db.commit()
        result = ensure_daily_digests(db, now=NOW, data_dir=tmp_path)
        assert result["enabled"] is False
        assert result["registered"] == 0
        assert db.execute(select(func.count(ReminderDelivery.id))).scalar() == 0
        db.commit()


def test_digest_delivery_is_idempotent_per_day(tmp_path):
    factory = make_session_factory(tmp_path / "digest-run.db")
    _write_reminders_config(tmp_path)
    with factory() as db:
        user = _seed_owner(db)
        db.commit()

        first = run_daily_digest(db, now=NOW, data_dir=tmp_path)
        assert first["registered"] == 1
        assert first["delivered"] == 1
        db.commit()

        anchor_id = digest_anchor_id(user.id)
        delivered_events = list(
            db.execute(
                select(TodoEvent).where(
                    TodoEvent.todo_item_id == anchor_id,
                    TodoEvent.event_type == "reminder_delivered",
                )
            ).scalars()
        )
        assert len(delivered_events) == 1
        assert "收件箱日报 2026-08-13" in delivered_events[0].did

        # Same-day rescan neither re-registers nor re-delivers.
        again = run_daily_digest(db, now=NOW + dt.timedelta(hours=2), data_dir=tmp_path)
        assert again["registered"] == 0
        assert again["delivered"] == 0
        assert db.execute(select(func.count(TodoEvent.id))).scalar() >= 1
        still_one = list(
            db.execute(
                select(TodoEvent).where(
                    TodoEvent.todo_item_id == anchor_id,
                    TodoEvent.event_type == "reminder_delivered",
                )
            ).scalars()
        )
        assert len(still_one) == 1
        db.commit()


def test_inbox_get_nudges_daily_digest(tmp_path):
    _write_reminders_config(tmp_path)
    factory = make_session_factory(tmp_path / "inbox-digest.db")
    with factory() as db:
        ids = _seed_board(db)
        db.add(
            ApiToken(
                token_hash=hash_token(AGENT_BEARER),
                actor_id="worker",
                label="inbox tests",
            )
        )
        _seed_owner(db)
        db.commit()
    client = TestClient(create_app(factory, data_dir=tmp_path))

    first = _get(client)
    assert first["digest"]["enabled"] is True
    assert first["digest"]["owners"] == 1
    assert first["digest"]["registered"] == 1
    assert first["digest"]["delivered"] == 1
    assert first["lanes"]["blocked"]["count"] == 1
    assert first["lanes"]["stale"]["count"] == 2

    second = _get(client)
    assert second["digest"]["registered"] == 0
    assert second["digest"]["delivered"] == 0
    assert second["lanes"]["decisions"]["count"] == 1

    with factory() as db:
        assert db.execute(select(func.count(ReminderDelivery.id))).scalar() == 1
        db.rollback()


def test_collect_inbox_counts_feed_digest_title(tmp_path):
    factory = make_session_factory(tmp_path / "digest-title.db")
    _write_reminders_config(tmp_path)
    with factory() as db:
        ids = _seed_board(db)
        user = _seed_owner(db)
        db.commit()

        data = collect_inbox(db, now=NOW)
        assert data["lanes"]["decisions"]["count"] == 1
        assert data["lanes"]["reviews"]["count"] == 1
        assert data["lanes"]["blocked"]["count"] == 1
        assert data["lanes"]["stale"]["count"] == 2

        ensure_daily_digests(db, now=NOW, data_dir=tmp_path)
        anchor = db.get(TodoItem, digest_anchor_id(user.id))
        assert anchor.title == (
            "收件箱日报 2026-08-13:待拍板 1 · 待质检 1 · 阻塞 1 · 超期未动 2"
        )
        db.commit()
