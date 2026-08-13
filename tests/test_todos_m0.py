"""Private todo hub M0 (schema v18): ownership, proposals, reminders, links.

The core invariant is privacy. A confirmed TodoItem is readable and writable
only by its owner user. Viewers are refused. Administrators do not inherit
read access; a compliance GET must carry an explicit reason and leave an
audit event. Agents may submit TodoProposal rows only after that owner
grants ``todo:propose``, and they cannot read another person's items.
"""

from __future__ import annotations

import datetime as dt

from fastapi.testclient import TestClient

from server.app import create_app
from server.db import Actor, ApiToken, User, make_session_factory, utcnow
from server.security import hash_password, hash_token


ALICE_PASS = "alice-pass-1"
BOB_PASS = "bob-pass-12"
ADMIN_PASS = "admin-pass-1"
VIEWER_PASS = "viewer-pass"
XIAOHAI_BEARER = "xiaohai-agent-bearer"
STRANGER_BEARER = "stranger-agent-bearer"


def _board(tmp_path):
    factory = make_session_factory(tmp_path / "todos.db")
    with factory() as db:
        db.add_all(
            [
                Actor(id="alice", kind="human", display_name="Alice"),
                Actor(id="bob", kind="human", display_name="Bob"),
                Actor(id="admin-op", kind="human", display_name="Operator"),
                Actor(id="xiaohai", kind="agent", display_name="Xiaohai"),
                Actor(id="stranger", kind="agent", display_name="Stranger"),
            ]
        )
        db.flush()
        db.add_all(
            [
                User(
                    username="alice",
                    password_hash=hash_password(ALICE_PASS),
                    role="member",
                    actor_id="alice",
                ),
                User(
                    username="bob",
                    password_hash=hash_password(BOB_PASS),
                    role="member",
                    actor_id="bob",
                ),
                User(
                    username="admin",
                    password_hash=hash_password(ADMIN_PASS),
                    role="admin",
                    actor_id="admin-op",
                ),
                User(
                    username="viewer",
                    password_hash=hash_password(VIEWER_PASS),
                    role="viewer",
                ),
            ]
        )
        db.add_all(
            [
                ApiToken(
                    token_hash=hash_token(XIAOHAI_BEARER),
                    actor_id="xiaohai",
                    label="xiaohai",
                ),
                ApiToken(
                    token_hash=hash_token(STRANGER_BEARER),
                    actor_id="stranger",
                    label="stranger",
                ),
            ]
        )
        db.commit()
    return TestClient(create_app(factory))


def login(client: TestClient, username: str, password: str) -> None:
    client.post("/api/auth/logout")
    response = client.post(
        "/api/auth/login", json={"username": username, "password": password}
    )
    assert response.status_code == 200, response.text


def xiaohai_headers() -> dict[str, str]:
    return {"Authorization": f"Bearer {XIAOHAI_BEARER}"}


def stranger_headers() -> dict[str, str]:
    return {"Authorization": f"Bearer {STRANGER_BEARER}"}


def test_owner_creates_completes_cancels_and_snoozes_with_events(tmp_path):
    client = _board(tmp_path)
    login(client, "alice", ALICE_PASS)
    created = client.post(
        "/api/todos",
        json={"title": "Buy oats", "notes": "small bag", "due_at": "2099-01-02"},
    )
    assert created.status_code == 200, created.text
    item_id = created.json()["id"]
    assert created.json()["status"] == "open"
    assert any(event["event_type"] == "created" for event in created.json()["events"])

    updated = client.post(
        f"/api/todos/{item_id}/update",
        json={"title": "Buy rolled oats"},
    )
    assert updated.status_code == 200
    assert updated.json()["title"] == "Buy rolled oats"

    snoozed = client.post(
        f"/api/todos/{item_id}/snooze",
        json={"due_at": "2099-02-01"},
    )
    assert snoozed.status_code == 200
    assert snoozed.json()["status"] == "snoozed"
    assert snoozed.json()["due_at"] == "2099-02-01"

    done = client.post(f"/api/todos/{item_id}/complete")
    assert done.status_code == 200
    assert done.status_code == 200
    assert done.json()["status"] == "done"
    types = [event["event_type"] for event in done.json()["events"]]
    assert types == ["created", "updated", "snoozed", "completed"]

    other = client.post("/api/todos", json={"title": "Cancel me"})
    cancelled = client.post(f"/api/todos/{other.json()['id']}/cancel")
    assert cancelled.status_code == 200
    assert cancelled.json()["status"] == "cancelled"
    assert cancelled.json()["events"][-1]["event_type"] == "cancelled"


def test_proposal_confirm_keeps_source_backlink_and_dedup(tmp_path):
    client = _board(tmp_path)
    login(client, "alice", ALICE_PASS)
    granted = client.post("/api/todos/grants", json={"actor_id": "xiaohai"})
    assert granted.status_code == 200
    assert "xiaohai" in granted.json()["actor_ids"]

    payload = {
        "title": "Call the clinic",
        "owner_username": "alice",
        "dedup_key": "session-42:msg-9",
        "source_session_id": 42,
        "source_message_id": "msg-9",
        "source_channel": "im",
        "source_backlink": "session:42#msg-9",
        "due_at": "2099-03-01",
    }
    first = client.post(
        "/api/todos/proposals", json=payload, headers=xiaohai_headers()
    )
    assert first.status_code == 200, first.text
    proposal_id = first.json()["id"]
    replay = client.post(
        "/api/todos/proposals", json=payload, headers=xiaohai_headers()
    )
    assert replay.status_code == 200
    assert replay.json()["id"] == proposal_id

    login(client, "alice", ALICE_PASS)
    confirmed = client.post(f"/api/todos/proposals/{proposal_id}/confirm")
    assert confirmed.status_code == 200, confirmed.text
    body = confirmed.json()
    assert body["status"] == "open"
    assert body["proposal_id"] == proposal_id
    assert body["source_session_id"] == 42
    assert body["source_message_id"] == "msg-9"
    assert body["source_backlink"] == "session:42#msg-9"
    assert body["source_channel"] == "im"


def test_reminder_registration_is_idempotent_and_due_query_is_owner_scoped(tmp_path):
    client = _board(tmp_path)
    login(client, "alice", ALICE_PASS)
    past = (utcnow() - dt.timedelta(minutes=5)).isoformat().replace("+00:00", "Z")
    created = client.post(
        "/api/todos",
        json={"title": "Take the keys", "remind_at": past},
    )
    assert created.status_code == 200, created.text
    item_id = created.json()["id"]
    first = client.post(
        f"/api/todos/{item_id}/reminders",
        json={"scheduled_for": past, "channel": "pending"},
    )
    second = client.post(
        f"/api/todos/{item_id}/reminders",
        json={"scheduled_for": past, "channel": "pending"},
    )
    assert first.status_code == second.status_code == 200
    assert first.json()["delivery_key"] == second.json()["delivery_key"]
    assert first.json()["id"] == second.json()["id"]

    due = client.get("/api/todos/reminders/due")
    assert due.status_code == 200
    keys = {row["delivery_key"] for row in due.json()["reminders"]}
    assert first.json()["delivery_key"] in keys

    login(client, "bob", BOB_PASS)
    other = client.get("/api/todos/reminders/due")
    assert other.status_code == 200
    assert other.json()["reminders"] == []


def test_promote_writes_bidirectional_task_link(tmp_path):
    client = _board(tmp_path)
    login(client, "alice", ALICE_PASS)
    created = client.post("/api/todos", json={"title": "Draft the brief"})
    item_id = created.json()["id"]
    promoted = client.post(f"/api/todos/{item_id}/promote")
    assert promoted.status_code == 200, promoted.text
    assert promoted.json()["status"] == "promoted"
    task_id = promoted.json()["task_id"]
    assert task_id
    assert promoted.json()["task"]["id"] == task_id
    assert item_id in promoted.json()["task"]["refs"]

    card = client.get(f"/api/tasks/{task_id}")
    assert card.status_code == 200
    assert item_id in card.json()["refs"]
    replay = client.post(f"/api/todos/{item_id}/promote")
    assert replay.status_code == 200
    assert replay.json()["task_id"] == task_id


def test_home_inbox_buckets(tmp_path):
    client = _board(tmp_path)
    login(client, "alice", ALICE_PASS)
    today = utcnow().date().isoformat()
    yesterday = (utcnow().date() - dt.timedelta(days=1)).isoformat()
    client.post("/api/todos", json={"title": "Due today", "due_at": today})
    client.post("/api/todos", json={"title": "Overdue", "due_at": yesterday})
    waiting = client.post("/api/todos", json={"title": "Needs Bob"})
    promoted = client.post(f"/api/todos/{waiting.json()['id']}/promote")
    task_id = promoted.json()["task_id"]
    handed = client.post(
        f"/api/tasks/{task_id}/update",
        json={"holder": "bob", "note": "handed to bob"},
    )
    assert handed.status_code == 200, handed.text

    client.post("/api/todos/grants", json={"actor_id": "xiaohai"})
    client.post(
        "/api/todos/proposals",
        json={
            "title": "Pending idea",
            "owner_username": "alice",
            "dedup_key": "pending-1",
        },
        headers=xiaohai_headers(),
    )

    login(client, "alice", ALICE_PASS)
    home = client.get("/api/todos/home")
    assert home.status_code == 200, home.text
    body = home.json()
    assert {row["title"] for row in body["due_today"]} == {"Due today"}
    assert {row["title"] for row in body["overdue"]} == {"Overdue"}
    assert {row["title"] for row in body["pending_proposals"]} == {"Pending idea"}
    assert {row["title"] for row in body["waiting_on_others"]} == {"Needs Bob"}
    assert body["waiting_on_others"][0]["task_holder"] == "bob"


def test_cross_user_read_is_forbidden(tmp_path):
    client = _board(tmp_path)
    login(client, "alice", ALICE_PASS)
    created = client.post("/api/todos", json={"title": "Alice only"})
    item_id = created.json()["id"]

    login(client, "bob", BOB_PASS)
    listed = client.get("/api/todos")
    assert listed.status_code == 200
    assert listed.json()["todos"] == []
    denied = client.get(f"/api/todos/{item_id}")
    assert denied.status_code == 403
    events = client.get(f"/api/todos/{item_id}/events")
    assert events.status_code == 403
    assert client.post(f"/api/todos/{item_id}/complete").status_code == 403
    assert client.post(
        f"/api/todos/{item_id}/update", json={"title": "stolen"}
    ).status_code == 403


def test_viewer_cannot_read_any_private_todo(tmp_path):
    client = _board(tmp_path)
    login(client, "alice", ALICE_PASS)
    created = client.post("/api/todos", json={"title": "Hidden from viewer"})
    item_id = created.json()["id"]

    login(client, "viewer", VIEWER_PASS)
    assert client.get("/api/todos").status_code == 403
    assert client.get("/api/todos/home").status_code == 403
    assert client.get(f"/api/todos/{item_id}").status_code == 403
    assert client.get("/api/todos/proposals").status_code == 403
    assert client.get("/api/todos/reminders/due").status_code == 403
    assert client.post("/api/todos", json={"title": "nope"}).status_code == 403


def test_agent_without_grant_cannot_propose_or_read(tmp_path):
    client = _board(tmp_path)
    login(client, "alice", ALICE_PASS)
    created = client.post("/api/todos", json={"title": "Owner item"})
    item_id = created.json()["id"]

    denied = client.post(
        "/api/todos/proposals",
        json={
            "title": "Unsolicited",
            "owner_username": "alice",
            "dedup_key": "no-grant",
        },
        headers=stranger_headers(),
    )
    assert denied.status_code == 403
    assert client.get("/api/todos", headers=stranger_headers()).status_code == 403
    assert client.get(f"/api/todos/{item_id}", headers=stranger_headers()).status_code == 403
    assert client.get("/api/todos/home", headers=stranger_headers()).status_code == 403


def test_granted_agent_can_propose_but_cannot_read_confirmed_item(tmp_path):
    client = _board(tmp_path)
    login(client, "alice", ALICE_PASS)
    client.post("/api/todos/grants", json={"actor_id": "xiaohai"})
    proposed = client.post(
        "/api/todos/proposals",
        json={
            "title": "From xiaohai",
            "owner_username": "alice",
            "dedup_key": "xiaohai-1",
            "source_backlink": "session:7#m1",
        },
        headers=xiaohai_headers(),
    )
    assert proposed.status_code == 200, proposed.text
    proposal_id = proposed.json()["id"]
    own = client.get(
        f"/api/todos/proposals/{proposal_id}", headers=xiaohai_headers()
    )
    assert own.status_code == 200

    login(client, "alice", ALICE_PASS)
    confirmed = client.post(f"/api/todos/proposals/{proposal_id}/confirm")
    item_id = confirmed.json()["id"]

    assert client.get(
        f"/api/todos/{item_id}", headers=xiaohai_headers()
    ).status_code == 403
    assert client.get("/api/todos", headers=xiaohai_headers()).status_code == 403
    listed = client.get("/api/todos/proposals", headers=xiaohai_headers())
    assert listed.status_code == 200
    assert listed.json()["proposals"][0]["id"] == proposal_id

    login(client, "bob", BOB_PASS)
    assert client.get(
        f"/api/todos/proposals/{proposal_id}"
    ).status_code == 403


def test_admin_read_requires_reason_and_writes_audit(tmp_path):
    client = _board(tmp_path)
    login(client, "alice", ALICE_PASS)
    created = client.post("/api/todos", json={"title": "Need a reason"})
    item_id = created.json()["id"]

    login(client, "admin", ADMIN_PASS)
    bare = client.get(f"/api/todos/{item_id}")
    assert bare.status_code == 403
    short = client.get(f"/api/todos/{item_id}", params={"reason": "look"})
    assert short.status_code == 403
    allowed = client.get(
        f"/api/todos/{item_id}",
        params={"reason": "compliance review"},
    )
    assert allowed.status_code == 200, allowed.text
    assert allowed.json()["title"] == "Need a reason"
    types = [event["event_type"] for event in allowed.json()["events"]]
    assert "admin_access" in types
    audit = allowed.json()["events"][-1]
    assert audit["reason"] == "compliance review"
    assert audit["who"] == "admin-op"

    listed = client.get("/api/todos")
    assert listed.status_code == 200
    assert listed.json()["todos"] == []
    assert client.post(f"/api/todos/{item_id}/complete").status_code == 403
