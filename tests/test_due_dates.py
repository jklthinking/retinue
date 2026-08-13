"""Due dates and depends_on echo on the task list and detail projections."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from server.app import create_app
from server.db import Actor, ApiToken, make_session_factory
from server.engine import create_task
from server.security import hash_token

AGENT_BEARER = "agent-bearer-for-due-tests"


@pytest.fixture()
def due_env(tmp_path):
    factory = make_session_factory(tmp_path / "due.db")
    with factory() as db:
        db.add(Actor(id="worker", kind="agent", display_name="Worker"))
        db.flush()
        db.add(
            ApiToken(
                token_hash=hash_token(AGENT_BEARER),
                actor_id="worker",
                label="due-date tests",
            )
        )
        prerequisite = create_task(
            db,
            title="Prepare ground",
            created_by="worker",
            holder="worker",
        )
        dependent = create_task(
            db,
            title="Build on ground",
            created_by="worker",
            holder="worker",
            depends_on=[prerequisite.id],
            due_at="2026-08-20",
        )
        db.commit()
        return TestClient(create_app(factory)), prerequisite.id, dependent.id


def _headers() -> dict[str, str]:
    return {"Authorization": f"Bearer {AGENT_BEARER}"}


def test_list_and_detail_echo_depends_on_and_due_at(due_env):
    client, prerequisite_id, dependent_id = due_env

    listed = client.get("/api/tasks", headers=_headers()).json()
    by_id = {task["id"]: task for task in listed}
    assert by_id[dependent_id]["depends_on"] == [prerequisite_id]
    assert by_id[dependent_id]["due_at"] == "2026-08-20"
    assert by_id[prerequisite_id]["depends_on"] == []
    assert by_id[prerequisite_id]["due_at"] is None

    detail = client.get(f"/api/tasks/{dependent_id}", headers=_headers()).json()
    assert detail["depends_on"] == [prerequisite_id]
    assert detail["due_at"] == "2026-08-20"

    paged = client.get("/api/tasks?page_size=10", headers=_headers()).json()
    paged_by_id = {task["id"]: task for task in paged["items"]}
    assert paged_by_id[dependent_id]["depends_on"] == [prerequisite_id]
    assert paged_by_id[dependent_id]["due_at"] == "2026-08-20"


def test_due_at_update_and_clear(due_env):
    client, _prerequisite_id, dependent_id = due_env

    updated = client.post(
        f"/api/tasks/{dependent_id}/update",
        headers=_headers(),
        json={"due_at": "2026-08-25", "note": "顺延到 25 日"},
    )
    assert updated.status_code == 200
    detail = client.get(f"/api/tasks/{dependent_id}", headers=_headers()).json()
    assert detail["due_at"] == "2026-08-25"

    cleared = client.post(
        f"/api/tasks/{dependent_id}/update",
        headers=_headers(),
        json={"due_at": "", "note": "取消截止日"},
    )
    assert cleared.status_code == 200
    detail = client.get(f"/api/tasks/{dependent_id}", headers=_headers()).json()
    assert detail["due_at"] is None


def test_due_at_rejects_non_calendar_values(due_env):
    client, _prerequisite_id, dependent_id = due_env

    bad = client.post(
        f"/api/tasks/{dependent_id}/update",
        headers=_headers(),
        json={"due_at": "下周三", "note": "尝试写入自然语言日期"},
    )
    assert bad.status_code == 422

    created = client.post(
        "/api/tasks",
        headers=_headers(),
        json={"title": "Bad due", "holder": "worker", "due_at": "2026-13-01"},
    )
    assert created.status_code == 422


def test_due_change_without_note_is_refused(due_env):
    client, _prerequisite_id, dependent_id = due_env

    response = client.post(
        f"/api/tasks/{dependent_id}/update",
        headers=_headers(),
        json={"due_at": "2026-08-30"},
    )
    assert response.status_code == 422
    detail = client.get(f"/api/tasks/{dependent_id}", headers=_headers()).json()
    assert detail["due_at"] == "2026-08-20"
