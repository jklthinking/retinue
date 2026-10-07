"""Personal-agenda M1 stage 2: proposal children, owner confirm, agent progress."""

from __future__ import annotations

import sqlite3

from sqlalchemy import inspect

from server.db import LATEST_SCHEMA_VERSION, make_session_factory, migrate_database

from test_todos_m0 import ALICE_PASS, _board, login, stranger_headers, xiaohai_headers


def test_owner_create_still_works_with_dual_dates(tmp_path):
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


def test_agent_post_todos_is_forbidden(tmp_path):
    client = _board(tmp_path)
    login(client, "alice", ALICE_PASS)
    granted = client.post("/api/todos/grants", json={"actor_id": "xiaohai"})
    assert granted.status_code == 200, granted.text

    denied = client.post(
        "/api/todos",
        json={"title": "Agent must not create"},
        headers=xiaohai_headers(),
    )
    assert denied.status_code == 403
    listed = client.get("/api/todos")
    assert listed.status_code == 200
    assert listed.json()["todos"] == []


def test_agent_proposal_children_need_owner_confirm(tmp_path):
    client = _board(tmp_path)
    login(client, "alice", ALICE_PASS)
    client.post("/api/todos/grants", json={"actor_id": "xiaohai"})

    proposed = client.post(
        "/api/todos/proposals",
        json={
            "title": "Share-out",
            "owner_username": "alice",
            "dedup_key": "share-out-children",
            "event_on": "2099-03-02",
            "due_at": "2099-03-01",
            "children": [
                {"title": "Write script", "due_at": "2099-03-01"},
                {"title": "Slides", "due_at": "2099-03-01", "progress": 10},
            ],
        },
        headers=xiaohai_headers(),
    )
    assert proposed.status_code == 200, proposed.text
    proposal = proposed.json()
    proposal_id = proposal["id"]
    assert proposal["event_on"] == "2099-03-02"
    assert proposal["due_at"] == "2099-03-01"
    assert proposal["parent_id"] is None
    assert proposal["status"] == "pending"
    assert proposal["todo_item_id"] is None
    assert [child["title"] for child in proposal["children"]] == [
        "Write script",
        "Slides",
    ]

    login(client, "alice", ALICE_PASS)
    before = client.get("/api/todos")
    assert before.status_code == 200
    assert before.json()["todos"] == []

    agent_confirm = client.post(
        f"/api/todos/proposals/{proposal_id}/confirm",
        headers=xiaohai_headers(),
    )
    assert agent_confirm.status_code == 403
    still_empty = client.get("/api/todos")
    assert still_empty.json()["todos"] == []

    login(client, "alice", ALICE_PASS)
    confirmed = client.post(f"/api/todos/proposals/{proposal_id}/confirm")
    assert confirmed.status_code == 200, confirmed.text
    parent = confirmed.json()
    assert parent["title"] == "Share-out"
    assert parent["event_on"] == "2099-03-02"
    assert parent["due_at"] == "2099-03-01"
    assert parent["parent_id"] is None
    assert parent["progress"] == 0
    assert parent["status"] == "open"
    assert parent["proposal_id"] == proposal_id

    listed = client.get("/api/todos")
    assert listed.status_code == 200
    by_title = {row["title"]: row for row in listed.json()["todos"]}
    assert set(by_title) == {"Share-out", "Write script", "Slides"}
    assert by_title["Share-out"]["id"] == parent["id"]
    assert by_title["Write script"]["parent_id"] == parent["id"]
    assert by_title["Slides"]["parent_id"] == parent["id"]
    assert by_title["Write script"]["progress"] == 0
    assert by_title["Slides"]["progress"] == 10
    assert by_title["Write script"]["status"] == "open"
    assert by_title["Slides"]["status"] == "open"


def test_granted_agent_progress_ungranted_forbidden(tmp_path):
    client = _board(tmp_path)
    login(client, "alice", ALICE_PASS)
    client.post("/api/todos/grants", json={"actor_id": "xiaohai"})
    proposed = client.post(
        "/api/todos/proposals",
        json={
            "title": "Write script",
            "owner_username": "alice",
            "dedup_key": "script-progress",
            "due_at": "2099-03-01",
        },
        headers=xiaohai_headers(),
    )
    assert proposed.status_code == 200, proposed.text
    proposal_id = proposed.json()["id"]

    login(client, "alice", ALICE_PASS)
    confirmed = client.post(f"/api/todos/proposals/{proposal_id}/confirm")
    assert confirmed.status_code == 200, confirmed.text

    seen = client.get(
        f"/api/todos/proposals/{proposal_id}", headers=xiaohai_headers()
    )
    assert seen.status_code == 200, seen.text
    item_id = seen.json()["todo_item_id"]
    assert item_id == confirmed.json()["id"]

    progressed = client.post(
        f"/api/todos/{item_id}/progress",
        json={"percent": 40, "note": "draft halfway"},
        headers=xiaohai_headers(),
    )
    assert progressed.status_code == 200, progressed.text
    body = progressed.json()
    assert body["progress"] == 40
    assert body["status"] == "open"
    assert any(event["event_type"] == "progress" for event in body["events"])

    denied = client.post(
        f"/api/todos/{item_id}/progress",
        json={"percent": 50, "note": "stranger tick"},
        headers=stranger_headers(),
    )
    assert denied.status_code == 403

    login(client, "alice", ALICE_PASS)
    loaded = client.get(f"/api/todos/{item_id}")
    assert loaded.status_code == 200
    assert loaded.json()["progress"] == 40
    assert loaded.json()["status"] == "open"


def test_progress_100_leaves_status_open(tmp_path):
    client = _board(tmp_path)
    login(client, "alice", ALICE_PASS)
    client.post("/api/todos/grants", json={"actor_id": "xiaohai"})
    proposed = client.post(
        "/api/todos/proposals",
        json={
            "title": "Slides",
            "owner_username": "alice",
            "dedup_key": "slides-100",
        },
        headers=xiaohai_headers(),
    )
    proposal_id = proposed.json()["id"]
    login(client, "alice", ALICE_PASS)
    item_id = client.post(
        f"/api/todos/proposals/{proposal_id}/confirm"
    ).json()["id"]

    done_bar = client.post(
        f"/api/todos/{item_id}/progress",
        json={"percent": 100, "note": "slides finished"},
        headers=xiaohai_headers(),
    )
    assert done_bar.status_code == 200, done_bar.text
    assert done_bar.json()["progress"] == 100
    assert done_bar.json()["status"] == "open"

    login(client, "alice", ALICE_PASS)
    loaded = client.get(f"/api/todos/{item_id}")
    assert loaded.json()["progress"] == 100
    assert loaded.json()["status"] == "open"


def test_progress_on_closed_todo_is_422(tmp_path):
    client = _board(tmp_path)
    login(client, "alice", ALICE_PASS)
    client.post("/api/todos/grants", json={"actor_id": "xiaohai"})
    item_id = client.post("/api/todos", json={"title": "Done work"}).json()["id"]
    assert client.post(f"/api/todos/{item_id}/complete").status_code == 200

    owner = client.post(
        f"/api/todos/{item_id}/progress",
        json={"percent": 90, "note": "too late"},
    )
    assert owner.status_code == 422, owner.text
    agent = client.post(
        f"/api/todos/{item_id}/progress",
        json={"percent": 90, "note": "agent too late"},
        headers=xiaohai_headers(),
    )
    assert agent.status_code == 422, agent.text
    loaded = client.get(f"/api/todos/{item_id}")
    assert loaded.json()["status"] == "done"
    assert loaded.json()["progress"] == 0


def test_http_progress_true_is_422(tmp_path):
    client = _board(tmp_path)
    login(client, "alice", ALICE_PASS)
    created = client.post("/api/todos", json={"title": "Talk"})
    assert created.status_code == 200, created.text
    item_id = created.json()["id"]

    rejected = client.post(
        f"/api/todos/{item_id}/progress",
        json={"percent": True, "note": "bool is not an int"},
    )
    assert rejected.status_code == 422

    loaded = client.get(f"/api/todos/{item_id}")
    assert loaded.json()["progress"] == 0
    assert loaded.json()["status"] == "open"


def test_schema_21_agenda_migration_survives_to_latest(tmp_path):
    db_path = tmp_path / "from-v21.db"
    factory = make_session_factory(db_path)
    assert LATEST_SCHEMA_VERSION == 26

    raw = sqlite3.connect(db_path)
    raw.execute("DROP INDEX IF EXISTS ix_todo_proposals_parent")
    raw.execute("ALTER TABLE todo_proposals DROP COLUMN event_on")
    raw.execute("ALTER TABLE todo_proposals DROP COLUMN parent_id")
    raw.execute("ALTER TABLE todo_proposals DROP COLUMN children_json")
    raw.execute("UPDATE schema_version SET version = 21 WHERE id = 1")
    raw.execute(
        "INSERT INTO users "
        "(username, password_hash, role, display_name, disabled, created_at, "
        "todo_propose_grants_json) "
        "VALUES (?, ?, ?, ?, ?, CURRENT_TIMESTAMP, ?)",
        ("legacy-owner", "unused-hash", "member", "", 0, "[]"),
    )
    owner_id = raw.execute(
        "SELECT id FROM users WHERE username = 'legacy-owner'"
    ).fetchone()[0]
    raw.execute(
        "INSERT INTO todo_proposals "
        "(id, owner_user_id, proposed_by, title, notes, status, "
        "created_at, updated_at) "
        "VALUES (?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)",
        (
            "proposal-20260821-001",
            owner_id,
            "xiaohai",
            "Legacy proposal",
            "",
            "pending",
        ),
    )
    raw.commit()
    columns = {row[1] for row in raw.execute("PRAGMA table_info(todo_proposals)")}
    assert "event_on" not in columns
    assert "parent_id" not in columns
    assert "children_json" not in columns
    version = raw.execute(
        "SELECT version FROM schema_version WHERE id = 1"
    ).fetchone()[0]
    assert version == 21
    raw.close()

    result = migrate_database(db_path)
    upgraded = make_session_factory(db_path)
    assert (result.from_version, result.to_version) == (21, 26)
    raw = sqlite3.connect(db_path)
    stored = raw.execute(
        "SELECT version FROM schema_version WHERE id = 1"
    ).fetchone()[0]
    raw.close()
    assert stored == 26 == LATEST_SCHEMA_VERSION

    inspector = inspect(upgraded.kw["bind"])
    proposal_columns = {
        column["name"] for column in inspector.get_columns("todo_proposals")
    }
    assert {"event_on", "parent_id", "children_json"} <= proposal_columns
    indexes = {
        index["name"]: index
        for index in inspector.get_indexes("todo_proposals")
    }
    assert indexes["ix_todo_proposals_parent"]["column_names"] == ["parent_id"]

    raw = sqlite3.connect(db_path)
    row = raw.execute(
        "SELECT event_on, parent_id, children_json, title, status "
        "FROM todo_proposals WHERE id = ?",
        ("proposal-20260821-001",),
    ).fetchone()
    raw.close()
    assert row is not None
    assert row[0] is None
    assert row[1] is None
    assert row[2] == "[]"
    assert row[3] == "Legacy proposal"
    assert row[4] == "pending"
