"""Personal-agenda M1 stage 5: PRD §10 分享会 walkthrough on a temp DB."""

from __future__ import annotations

import datetime as dt

from server.db import utcnow

from test_todos_m0 import ALICE_PASS, _board, login, xiaohai_headers


def test_share_out_walkthrough(tmp_path):
    client = _board(tmp_path)
    login(client, "alice", ALICE_PASS)
    today = utcnow().date().isoformat()
    tomorrow = (utcnow().date() + dt.timedelta(days=1)).isoformat()

    # 1. Parent 「分享会」 tomorrow; children due today.
    parent = client.post(
        "/api/todos",
        json={"title": "分享会", "event_on": tomorrow},
    )
    assert parent.status_code == 200, parent.text
    parent_id = parent.json()["id"]
    assert parent.json()["status"] == "open"
    assert parent.json()["ready_to_close"] is False

    script = client.post(
        "/api/todos",
        json={"title": "写完讲稿", "due_at": today, "parent_id": parent_id},
    )
    assert script.status_code == 200, script.text
    script_id = script.json()["id"]

    slides = client.post(
        "/api/todos",
        json={"title": "做好课件", "due_at": today, "parent_id": parent_id},
    )
    assert slides.status_code == 200, slides.text
    slides_id = slides.json()["id"]

    # 2. home_inbox: due_today has the two children; events_tomorrow has 分享会;
    # anytime does not.
    home = client.get("/api/todos/home")
    assert home.status_code == 200, home.text
    body = home.json()
    assert {row["title"] for row in body["due_today"]} == {"写完讲稿", "做好课件"}
    assert {row["title"] for row in body["events_tomorrow"]} == {"分享会"}
    assert {row["title"] for row in body["anytime"]} == set()
    event = body["events_tomorrow"][0]
    assert event["id"] == parent_id
    assert event["ready_to_close"] is False
    assert {child["title"] for child in event["children"]} == {"写完讲稿", "做好课件"}

    # 3. 讲稿 60; 课件 0; parent not done.
    scripted = client.post(
        f"/api/todos/{script_id}/update", json={"progress": 60}
    )
    assert scripted.status_code == 200, scripted.text
    assert scripted.json()["progress"] == 60
    assert scripted.json()["status"] == "open"
    loaded_slides = client.get(f"/api/todos/{slides_id}")
    assert loaded_slides.json()["progress"] == 0
    assert loaded_slides.json()["status"] == "open"
    loaded_parent = client.get(f"/api/todos/{parent_id}")
    assert loaded_parent.json()["status"] == "open"
    assert loaded_parent.json()["ready_to_close"] is False

    # 4. Granted agent POST progress 40 on 课件; status stays open.
    granted = client.post("/api/todos/grants", json={"actor_id": "xiaohai"})
    assert granted.status_code == 200, granted.text
    progressed = client.post(
        f"/api/todos/{slides_id}/progress",
        json={"percent": 40, "note": "slides sketched"},
        headers=xiaohai_headers(),
    )
    assert progressed.status_code == 200, progressed.text
    assert progressed.json()["progress"] == 40
    assert progressed.json()["status"] == "open"

    login(client, "alice", ALICE_PASS)
    still_open = client.get(f"/api/todos/{slides_id}")
    assert still_open.json()["progress"] == 40
    assert still_open.json()["status"] == "open"

    # 5. Owner completes both children; parent is ready to close; owner closes it.
    done_script = client.post(f"/api/todos/{script_id}/complete")
    assert done_script.status_code == 200, done_script.text
    assert done_script.json()["status"] == "done"
    done_slides = client.post(f"/api/todos/{slides_id}/complete")
    assert done_slides.status_code == 200, done_slides.text
    assert done_slides.json()["status"] == "done"

    home_ready = client.get("/api/todos/home")
    assert home_ready.status_code == 200, home_ready.text
    ready_body = home_ready.json()
    assert {row["title"] for row in ready_body["due_today"]} == set()
    share = next(row for row in ready_body["events_tomorrow"] if row["title"] == "分享会")
    assert share["status"] == "open"
    assert share["ready_to_close"] is True
    assert {child["title"] for child in share["children"]} == {"写完讲稿", "做好课件"}
    assert all(child["status"] == "done" for child in share["children"])
    parent_ready = client.get(f"/api/todos/{parent_id}")
    assert parent_ready.json()["ready_to_close"] is True
    assert parent_ready.json()["status"] == "open"

    closed = client.post(f"/api/todos/{parent_id}/complete")
    assert closed.status_code == 200, closed.text
    assert closed.json()["status"] == "done"
    assert closed.json()["ready_to_close"] is False
    after_close = client.get("/api/todos/home").json()
    assert {row["title"] for row in after_close["events_tomorrow"]} == set()

    # 6. 「整理神思记配色笔记」 anytime only, not in due_today.
    notes = client.post("/api/todos", json={"title": "整理神思记配色笔记"})
    assert notes.status_code == 200, notes.text
    assert notes.json()["due_at"] is None
    assert notes.json()["event_on"] is None
    home_notes = client.get("/api/todos/home").json()
    assert {row["title"] for row in home_notes["anytime"]} == {"整理神思记配色笔记"}
    assert "整理神思记配色笔记" not in {
        row["title"] for row in home_notes["due_today"]
    }

    # 7. Promote 课件 (open todo — the walkthrough child is already done).
    # Task progress copies onto the todo and must not auto-complete it.
    promotable = client.post(
        "/api/todos",
        json={"title": "做好课件", "due_at": today},
    )
    assert promotable.status_code == 200, promotable.text
    promo_id = promotable.json()["id"]
    assert promotable.json()["status"] == "open"
    assert promotable.json()["progress"] == 0

    promoted = client.post(f"/api/todos/{promo_id}/promote")
    assert promoted.status_code == 200, promoted.text
    assert promoted.json()["status"] == "promoted"
    task_id = promoted.json()["task_id"]
    assert task_id
    card = client.get(f"/api/tasks/{task_id}")
    assert card.status_code == 200, card.text
    assert promo_id in card.json()["refs"]

    started = client.post(
        f"/api/tasks/{task_id}/update",
        json={"status": "doing", "note": "start slides card"},
    )
    assert started.status_code == 200, started.text
    after_start = client.get(f"/api/todos/{promo_id}")
    assert after_start.json()["progress"] == 0
    assert after_start.json()["status"] == "promoted"

    owner_tick = client.post(
        f"/api/tasks/{task_id}/update",
        json={"progress": 55, "note": "owner task progress"},
    )
    assert owner_tick.status_code == 200, owner_tick.text
    assert owner_tick.json()["progress"] == 55
    copied = client.get(f"/api/todos/{promo_id}")
    assert copied.json()["progress"] == 55
    assert copied.json()["status"] == "promoted"

    handed = client.post(
        f"/api/tasks/{task_id}/update",
        json={"holder": "xiaohai", "note": "xiaohai takes slides"},
    )
    assert handed.status_code == 200, handed.text
    client.post("/api/auth/logout")
    agent_tick = client.post(
        f"/api/tasks/{task_id}/update",
        json={"progress": 80, "note": "agent task progress"},
        headers=xiaohai_headers(),
    )
    assert agent_tick.status_code == 200, agent_tick.text
    assert agent_tick.json()["progress"] == 80

    login(client, "alice", ALICE_PASS)
    after_agent = client.get(f"/api/todos/{promo_id}")
    assert after_agent.json()["progress"] == 80
    assert after_agent.json()["status"] == "promoted"

    client.post("/api/auth/logout")
    full_bar = client.post(
        f"/api/tasks/{task_id}/update",
        json={"progress": 100, "note": "card finished"},
        headers=xiaohai_headers(),
    )
    assert full_bar.status_code == 200, full_bar.text
    assert full_bar.json()["progress"] == 100
    assert full_bar.json()["status"] == "doing"

    login(client, "alice", ALICE_PASS)
    still_todo = client.get(f"/api/todos/{promo_id}")
    assert still_todo.json()["progress"] == 100
    assert still_todo.json()["status"] == "promoted"
    assert still_todo.json()["status"] != "done"
