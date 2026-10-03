"""Personal-agenda M1 stage 1: event_on, parent_id, and progress on todos."""

from __future__ import annotations

import datetime as dt

import pytest
from sqlalchemy import select

from core.protocol.task import ProtocolError
from server.db import User, utcnow
from server.deps import Principal
from server.todos import complete_item, create_item, get_item, item_to_dict

from test_todos_m0 import ALICE_PASS, BOB_PASS, _board, login


def test_create_with_event_on_and_due_at(tmp_path):
    client = _board(tmp_path)
    login(client, "alice", ALICE_PASS)
    created = client.post(
        "/api/todos",
        json={
            "title": "Share-out",
            "event_on": "2099-03-02",
            "due_at": "2099-03-01",
        },
    )
    assert created.status_code == 200, created.text
    body = created.json()
    assert body["event_on"] == "2099-03-02"
    assert body["due_at"] == "2099-03-01"
    assert body["parent_id"] is None
    assert body["progress"] == 0
    assert body["status"] == "open"


def test_create_with_neither_date_is_anytime_ready(tmp_path):
    client = _board(tmp_path)
    login(client, "alice", ALICE_PASS)
    created = client.post("/api/todos", json={"title": "Color notes someday"})
    assert created.status_code == 200, created.text
    body = created.json()
    assert body["event_on"] is None
    assert body["due_at"] is None
    assert body["progress"] == 0
    assert body["status"] == "open"


def test_parent_one_level_ok_grandchild_rejected(tmp_path):
    client = _board(tmp_path)
    login(client, "alice", ALICE_PASS)
    parent = client.post(
        "/api/todos",
        json={"title": "Share-out", "event_on": "2099-03-02"},
    )
    assert parent.status_code == 200, parent.text
    parent_id = parent.json()["id"]

    child = client.post(
        "/api/todos",
        json={"title": "Write script", "due_at": "2099-03-01", "parent_id": parent_id},
    )
    assert child.status_code == 200, child.text
    assert child.json()["parent_id"] == parent_id
    child_id = child.json()["id"]

    sibling = client.post(
        "/api/todos",
        json={"title": "Slides", "due_at": "2099-03-01", "parent_id": parent_id},
    )
    assert sibling.status_code == 200, sibling.text
    assert sibling.json()["parent_id"] == parent_id

    grandchild = client.post(
        "/api/todos",
        json={"title": "Too deep", "parent_id": child_id},
    )
    assert grandchild.status_code == 422
    assert "child cannot be a parent" in grandchild.json()["detail"]

    other_root = client.post("/api/todos", json={"title": "Another event"})
    nested_parent = client.post(
        f"/api/todos/{parent_id}/update",
        json={"parent_id": other_root.json()["id"]},
    )
    assert nested_parent.status_code == 422
    assert "cannot become a child" in nested_parent.json()["detail"]


def test_missing_and_foreign_parent_rejected(tmp_path):
    client = _board(tmp_path)
    login(client, "bob", BOB_PASS)
    bob_item = client.post("/api/todos", json={"title": "Bob's event"})
    assert bob_item.status_code == 200, bob_item.text
    bob_id = bob_item.json()["id"]

    login(client, "alice", ALICE_PASS)
    missing = client.post(
        "/api/todos",
        json={"title": "Orphan", "parent_id": "todo-20990101-999"},
    )
    assert missing.status_code == 422
    assert "does not exist" in missing.json()["detail"]

    foreign = client.post(
        "/api/todos",
        json={"title": "Stolen parent", "parent_id": bob_id},
    )
    assert foreign.status_code == 422
    assert "same owner" in foreign.json()["detail"]


def test_progress_bounds(tmp_path):
    client = _board(tmp_path)
    login(client, "alice", ALICE_PASS)
    zero = client.post("/api/todos", json={"title": "Just started", "progress": 0})
    assert zero.status_code == 200, zero.text
    assert zero.json()["progress"] == 0

    full = client.post("/api/todos", json={"title": "Finished work", "progress": 100})
    assert full.status_code == 200, full.text
    assert full.json()["progress"] == 100
    assert full.json()["status"] == "open"

    too_high = client.post("/api/todos", json={"title": "Over", "progress": 101})
    assert too_high.status_code == 422


def _alice_principal(client) -> tuple:
    factory = client.app.state.session_factory
    db = factory()
    user = db.execute(select(User).where(User.username == "alice")).scalar_one()
    principal = Principal(
        kind="user",
        name="alice",
        actor_id="alice",
        role="member",
        user=user,
    )
    return db, principal


def test_http_progress_rejects_json_bool(tmp_path):
    client = _board(tmp_path)
    login(client, "alice", ALICE_PASS)

    for value in (True, False):
        created = client.post(
            "/api/todos", json={"title": "Bool progress", "progress": value}
        )
        assert created.status_code == 422, created.text

    zero = client.post("/api/todos", json={"title": "Zero via HTTP", "progress": 0})
    assert zero.status_code == 200, zero.text
    assert zero.json()["progress"] == 0

    full = client.post("/api/todos", json={"title": "Full via HTTP", "progress": 100})
    assert full.status_code == 200, full.text
    assert full.json()["progress"] == 100

    too_high = client.post("/api/todos", json={"title": "Over via HTTP", "progress": 101})
    assert too_high.status_code == 422, too_high.text

    updated = client.post(
        f"/api/todos/{zero.json()['id']}/update", json={"progress": True}
    )
    assert updated.status_code == 422, updated.text


def test_invalid_event_on_names_the_field(tmp_path):
    client = _board(tmp_path)
    login(client, "alice", ALICE_PASS)
    bad = client.post(
        "/api/todos", json={"title": "Bad day", "event_on": "not-a-date"}
    )
    assert bad.status_code == 422, bad.text
    detail = bad.json()["detail"]
    assert "event_on" in detail
    assert "due_at" not in detail


def test_progress_rejects_bool(tmp_path):
    client = _board(tmp_path)
    login(client, "alice", ALICE_PASS)
    db, principal = _alice_principal(client)
    try:
        with pytest.raises(ProtocolError, match="progress must be an integer"):
            create_item(db, principal, title="Nope", progress=True)
    finally:
        db.close()


def test_update_event_on_and_progress_without_completing(tmp_path):
    client = _board(tmp_path)
    login(client, "alice", ALICE_PASS)
    created = client.post("/api/todos", json={"title": "Talk"})
    item_id = created.json()["id"]
    assert created.json()["event_on"] is None
    assert created.json()["progress"] == 0

    dated = client.post(
        f"/api/todos/{item_id}/update",
        json={"event_on": "2099-04-01", "progress": 40},
    )
    assert dated.status_code == 200, dated.text
    assert dated.json()["event_on"] == "2099-04-01"
    assert dated.json()["progress"] == 40
    assert dated.json()["status"] == "open"

    cleared = client.post(
        f"/api/todos/{item_id}/update",
        json={"event_on": ""},
    )
    assert cleared.status_code == 200, cleared.text
    assert cleared.json()["event_on"] is None
    assert cleared.json()["progress"] == 40
    assert cleared.json()["status"] == "open"


def test_complete_item_does_not_force_progress_100(tmp_path):
    client = _board(tmp_path)
    login(client, "alice", ALICE_PASS)
    created = client.post(
        "/api/todos", json={"title": "Halfway", "progress": 40}
    )
    item_id = created.json()["id"]
    done = client.post(f"/api/todos/{item_id}/complete")
    assert done.status_code == 200, done.text
    assert done.json()["status"] == "done"
    assert done.json()["progress"] == 40


def test_item_to_dict_has_agenda_fields_and_due_at(tmp_path):
    client = _board(tmp_path)
    login(client, "alice", ALICE_PASS)
    created = client.post(
        "/api/todos",
        json={
            "title": "Dict check",
            "due_at": "2099-05-01",
            "event_on": "2099-05-02",
            "progress": 15,
        },
    )
    assert created.status_code == 200, created.text
    body = created.json()
    assert body["due_at"] == "2099-05-01"
    assert body["event_on"] == "2099-05-02"
    assert body["parent_id"] is None
    assert body["progress"] == 15

    db, principal = _alice_principal(client)
    try:
        loaded = get_item(db, body["id"])
        assert loaded is not None
        payload = item_to_dict(db, loaded)
        assert "due_at" in payload
        assert payload["due_at"] == "2099-05-01"
        assert payload["event_on"] == "2099-05-02"
        assert payload["parent_id"] is None
        assert payload["progress"] == 15
        completed = complete_item(db, principal, loaded)
        assert completed.status == "done"
        assert completed.progress == 15
        assert item_to_dict(db, completed)["due_at"] == "2099-05-01"
    finally:
        db.close()


def test_home_inbox_events_tomorrow_and_anytime(tmp_path):
    client = _board(tmp_path)
    login(client, "alice", ALICE_PASS)
    today = utcnow().date()
    tomorrow = today + dt.timedelta(days=1)
    yesterday = today - dt.timedelta(days=1)

    parent = client.post(
        "/api/todos",
        json={"title": "Share-out", "event_on": tomorrow.isoformat()},
    )
    assert parent.status_code == 200, parent.text
    parent_id = parent.json()["id"]
    script = client.post(
        "/api/todos",
        json={
            "title": "Write script",
            "due_at": today.isoformat(),
            "parent_id": parent_id,
            "progress": 60,
        },
    )
    assert script.status_code == 200, script.text
    slides = client.post(
        "/api/todos",
        json={
            "title": "Slides",
            "due_at": today.isoformat(),
            "parent_id": parent_id,
        },
    )
    assert slides.status_code == 200, slides.text
    anytime = client.post("/api/todos", json={"title": "Color notes someday"})
    assert anytime.status_code == 200, anytime.text
    due_tomorrow = client.post(
        "/api/todos",
        json={"title": "Pack bag", "due_at": tomorrow.isoformat()},
    )
    assert due_tomorrow.status_code == 200, due_tomorrow.text
    overdue = client.post(
        "/api/todos",
        json={"title": "Return book", "due_at": yesterday.isoformat()},
    )
    assert overdue.status_code == 200, overdue.text
    done = client.post("/api/todos", json={"title": "Already finished"})
    assert done.status_code == 200, done.text
    completed = client.post(f"/api/todos/{done.json()['id']}/complete")
    assert completed.status_code == 200, completed.text

    home = client.get("/api/todos/home")
    assert home.status_code == 200, home.text
    body = home.json()
    for key in (
        "pending_proposals",
        "due_today",
        "overdue",
        "waiting_on_others",
        "events_tomorrow",
        "anytime",
    ):
        assert key in body

    assert {row["title"] for row in body["events_tomorrow"]} == {"Share-out"}
    event = body["events_tomorrow"][0]
    assert event["event_on"] == tomorrow.isoformat()
    assert event["due_at"] is None
    assert event["parent_id"] is None
    assert event["progress"] == 0
    assert {child["title"] for child in event["children"]} == {"Write script", "Slides"}
    script_child = next(c for c in event["children"] if c["title"] == "Write script")
    assert script_child["parent_id"] == parent_id
    assert script_child["due_at"] == today.isoformat()
    assert script_child["progress"] == 60

    assert {row["title"] for row in body["due_today"]} == {"Write script", "Slides"}
    for row in body["due_today"]:
        assert row["due_at"] == today.isoformat()
        assert "event_on" in row
        assert "parent_id" in row
        assert "progress" in row
        assert "children" in row

    assert {row["title"] for row in body["anytime"]} == {"Color notes someday"}
    note = body["anytime"][0]
    assert note["event_on"] is None
    assert note["due_at"] is None
    assert note["progress"] == 0
    assert "Already finished" not in {row["title"] for row in body["anytime"]}
    assert "Share-out" not in {row["title"] for row in body["anytime"]}

    assert {row["title"] for row in body["overdue"]} == {"Return book"}
    assert body["pending_proposals"] == []
    assert body["waiting_on_others"] == []


def test_home_inbox_item_with_both_dates_lands_in_today_and_tomorrow(tmp_path):
    client = _board(tmp_path)
    login(client, "alice", ALICE_PASS)
    today = utcnow().date()
    tomorrow = today + dt.timedelta(days=1)
    created = client.post(
        "/api/todos",
        json={
            "title": "Same row both dates",
            "event_on": tomorrow.isoformat(),
            "due_at": today.isoformat(),
        },
    )
    assert created.status_code == 200, created.text
    body = client.get("/api/todos/home").json()
    assert {row["title"] for row in body["due_today"]} == {"Same row both dates"}
    assert {row["title"] for row in body["events_tomorrow"]} == {"Same row both dates"}
    assert body["anytime"] == []
