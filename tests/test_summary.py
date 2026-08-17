"""Summary read model: first-screen lanes, counts, and updated_since deltas."""

from __future__ import annotations

import datetime as dt
from urllib.parse import quote

import pytest
from fastapi.testclient import TestClient

from server.app import create_app
from server.db import Actor, ApiToken, Approval, Task, make_session_factory
from server.engine import create_task, update_task
from server.security import hash_token

AGENT_BEARER = "agent-bearer-for-summary-tests"
TODAY = "2026-08-20"


@pytest.fixture()
def summary_env(tmp_path):
    factory = make_session_factory(tmp_path / "summary.db")
    with factory() as db:
        db.add(Actor(id="owner", kind="human", display_name="Owner"))
        db.add(Actor(id="worker", kind="agent", display_name="Worker"))
        db.add(
            Actor(
                id="ghost",
                kind="agent",
                display_name="Ghost",
                last_seen_at=dt.datetime(2026, 8, 1, 8, 0, 0),
            )
        )
        db.flush()
        db.add(
            ApiToken(
                token_hash=hash_token(AGENT_BEARER),
                actor_id="worker",
                label="summary tests",
            )
        )
        due = create_task(
            db, title="Due today card", created_by="owner", holder="worker",
            due_at="2026-08-20",
        )
        over_one = create_task(
            db, title="Overdue one", created_by="owner", holder="worker",
            due_at="2026-08-18",
        )
        create_task(
            db, title="Overdue two", created_by="owner", holder="worker",
            due_at="2026-08-19",
        )
        blocked = create_task(
            db, title="Blocked card", created_by="owner", holder="worker",
        )
        update_task(db, blocked, who="owner", is_privileged=True, status="doing",
                note="开始处理")
        update_task(
            db, blocked, who="owner", is_privileged=True,
            status="blocked", blocked_reason="等上游数据", note="卡住",
        )
        lost = create_task(
            db, title="Ghost held card", created_by="owner", holder="ghost",
        )
        update_task(db, lost, who="owner", is_privileged=True, status="doing",
                note="开始处理")
        done = create_task(
            db, title="Finished card", created_by="owner", holder="worker",
        )
        update_task(db, done, who="owner", is_privileged=True, status="doing",
                note="开始处理")
        update_task(db, done, who="owner", is_privileged=True, status="done",
                note="验收通过")
        db.add(
            Approval(
                task_id=blocked.id,
                stage_index=0,
                requested_by="worker",
                token_hash=hash_token("approval-link-token-for-summary-tests"),
            )
        )
        db.commit()
        ids = {
            "due": due.id,
            "over_one": over_one.id,
            "blocked": blocked.id,
            "lost": lost.id,
            "done": done.id,
        }
    return TestClient(create_app(factory)), factory, ids


def _headers() -> dict[str, str]:
    return {"Authorization": f"Bearer {AGENT_BEARER}"}


def _get(client, query: str = ""):
    response = client.get(f"/api/summary?today={TODAY}{query}", headers=_headers())
    assert response.status_code == 200, response.text
    return response.json()


def test_summary_requires_auth(summary_env):
    client, _factory, _ids = summary_env
    assert client.get("/api/summary").status_code == 401


def test_summary_full_shape(summary_env):
    client, _factory, ids = summary_env
    data = _get(client)

    assert data["partial"] is False
    assert data["today"] == TODAY
    assert data["generated_at"]

    lanes = data["lanes"]
    assert lanes["due_today"]["count"] == 1
    assert [item["id"] for item in lanes["due_today"]["items"]] == [ids["due"]]
    assert lanes["overdue"]["count"] == 2
    assert lanes["blocked"]["count"] == 1
    assert lanes["blocked"]["items"][0]["blocked_reason"] == "等上游数据"
    assert lanes["decisions"]["count"] == 1
    assert lanes["decisions"]["items"][0]["task_id"] == ids["blocked"]

    lost = lanes["lost_executors"]
    assert lost["count"] == 1
    assert lost["items"][0]["task"]["id"] == ids["lost"]
    assert lost["items"][0]["actor"]["id"] == "ghost"
    assert lost["items"][0]["actor"]["online"] is False

    counts = data["task_counts"]
    assert counts["queued"] == 3
    assert counts["doing"] == 1
    assert counts["blocked"] == 1
    assert counts["done"] == 1

    assert len(data["tasks"]) == 6
    assert {actor["id"] for actor in data["actors"]} >= {"worker", "ghost"}
    assert len(data["approvals"]) == 1

    recent = data["recent_events"]
    assert recent
    assert {"who", "did", "at", "task_id", "task_title"} <= set(recent[0])


def test_summary_today_param_moves_due_lanes(summary_env):
    client, _factory, _ids = summary_env
    # 2026-08-17 is before every due date: nothing is due-today or overdue yet.
    response = client.get("/api/summary?today=2026-08-17", headers=_headers())
    assert response.status_code == 200
    lanes = response.json()["lanes"]
    assert lanes["due_today"]["count"] == 0
    assert lanes["overdue"]["count"] == 0
    assert response.json()["today"] == "2026-08-17"


def test_summary_lane_limit_caps_items_not_counts(summary_env):
    client, _factory, _ids = summary_env
    data = _get(client, "&lane_limit=1")
    overdue = data["lanes"]["overdue"]
    assert overdue["count"] == 2
    assert len(overdue["items"]) == 1


def test_summary_updated_since_returns_only_changes(summary_env):
    client, factory, _ids = summary_env
    watermark = quote(_get(client)["generated_at"])

    # Nothing changed after the watermark: the delta is empty but still partial.
    delta = _get(client, f"&updated_since={watermark}")
    assert delta["partial"] is True
    assert delta["tasks"] == []
    # Aggregates stay complete on incremental polls.
    assert delta["lanes"]["blocked"]["count"] == 1
    assert delta["task_counts"]["queued"] == 3

    with factory() as db:
        fresh = create_task(
            db, title="Late arrival", created_by="owner", holder="worker",
        )
        db.commit()
        fresh_id = fresh.id

    delta = _get(client, f"&updated_since={watermark}")
    assert [task["id"] for task in delta["tasks"]] == [fresh_id]
    assert delta["task_counts"]["queued"] == 4


def test_summary_include_tasks_false_omits_task_list(summary_env):
    client, _factory, _ids = summary_env
    data = _get(client, "&include_tasks=false")
    assert data["tasks"] is None
    assert data["lanes"]["overdue"]["count"] == 2
    assert data["task_counts"]["queued"] == 3


def test_summary_include_archived(summary_env):
    client, factory, ids = summary_env
    with factory() as db:
        task = db.get(Task, ids["done"])
        task.archived = True
        db.commit()

    data = _get(client)
    assert ids["done"] not in {task["id"] for task in data["tasks"]}
    assert "done" not in data["task_counts"]

    archived = _get(client, "&include_archived=true")
    assert ids["done"] in {task["id"] for task in archived["tasks"]}
    # Lanes and counts always exclude archived rows.
    assert "done" not in archived["task_counts"]


def test_summary_rejects_bad_params(summary_env):
    client, _factory, _ids = summary_env
    assert client.get(
        "/api/summary?updated_since=not-a-date", headers=_headers()
    ).status_code == 422
    assert client.get("/api/summary?today=13-13", headers=_headers()).status_code == 422
    assert client.get(
        "/api/summary?lane_limit=0", headers=_headers()
    ).status_code == 422
