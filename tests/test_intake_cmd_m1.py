"""Intake command grammar M1: in-chat progress, note, status, done."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from server.app import create_app
from server.db import (
    Actor,
    ApiToken,
    ChannelToken,
    ChannelUser,
    Task,
    TaskEvent,
    User,
    make_session_factory,
)
from server.security import hash_password, hash_token

FEISHU_BEARER = "feishu-channel-bearer"
AGENT_BEARER = "plain-agent-bearer"


@pytest.fixture()
def board(tmp_path):
    factory = make_session_factory(tmp_path / "board.db")
    with factory() as db:
        db.add_all(
            [
                Actor(id="lark-alice", kind="human", display_name="Alice"),
                Actor(id="lark-bob", kind="human", display_name="Bob"),
                Actor(id="admin-op", kind="human", display_name="Operator"),
                Actor(id="agent-x", kind="agent", display_name="Agent X"),
            ]
        )
        db.flush()
        db.add_all(
            [
                User(
                    username="admin",
                    password_hash=hash_password("admin-pass-1"),
                    role="admin",
                    actor_id="admin-op",
                ),
            ]
        )
        db.add(
            ApiToken(
                token_hash=hash_token(AGENT_BEARER),
                actor_id="agent-x",
                label="plain agent",
            )
        )
        db.add(
            ChannelToken(
                token_hash=hash_token(FEISHU_BEARER),
                channel_id="feishu",
                label="lark bot",
            )
        )
        db.add_all(
            [
                ChannelUser(
                    channel_id="feishu",
                    channel_user_id="ou_alice",
                    actor_id="lark-alice",
                    display_name="Alice",
                ),
                ChannelUser(
                    channel_id="feishu",
                    channel_user_id="ou_bob",
                    actor_id="lark-bob",
                    display_name="Bob",
                ),
            ]
        )
        db.commit()
    return TestClient(create_app(factory)), factory


def feishu_headers() -> dict[str, str]:
    return {"Authorization": f"Bearer {FEISHU_BEARER}"}


def admin_login(client: TestClient) -> None:
    response = client.post(
        "/api/auth/login", json={"username": "admin", "password": "admin-pass-1"}
    )
    assert response.status_code == 200


def open_hall_card(client: TestClient, *, text: str, message_id: str) -> dict:
    response = client.post(
        "/api/intake/feishu/webhook",
        json={"sender_id": "ou_alice", "text": text, "message_id": message_id},
        headers=feishu_headers(),
    )
    assert response.status_code == 200
    return response.json()


def start_as_holder(client: TestClient, task_id: str, holder: str = "lark-alice") -> None:
    admin_login(client)
    response = client.post(
        f"/api/tasks/{task_id}/update",
        json={
            "status": "doing",
            "holder": holder,
            "note": "测试接手开始执行",
        },
    )
    assert response.status_code == 200, response.text
    client.post("/api/auth/logout")


def chain_len(factory, task_id: str) -> int:
    with factory() as db:
        return (
            db.query(TaskEvent).filter(TaskEvent.task_id == task_id).count()
        )


def test_default_open_path_unchanged(board):
    client, _factory = board
    body = open_hall_card(
        client, text="整理一份发布会纪要\n第二条:尽快", message_id="om_m1_open"
    )
    assert body["created_by"] == "lark-alice"
    assert body["status"] == "queued"
    assert body["intent"] == "open"
    assert body["task_id"] in body["reply"]
    assert "receipt" in body

    card = client.get(f"/api/tasks/{body['task_id']}", headers=feishu_headers())
    assert card.status_code == 200
    data = card.json()
    assert data["open_dispatch"] is True
    assert data["holder"] == "lark-alice"


def test_open_prefix_strips_command(board):
    client, _factory = board
    body = open_hall_card(client, text="开卡 写周报提纲", message_id="om_m1_new")
    assert body["intent"] == "open"
    card = client.get(f"/api/tasks/{body['task_id']}", headers=feishu_headers())
    assert card.json()["title"] == "写周报提纲"


def test_progress_appends_event(board):
    client, factory = board
    opened = open_hall_card(client, text="进度目标卡", message_id="om_m1_p0")
    task_id = opened["task_id"]
    start_as_holder(client, task_id)
    before = chain_len(factory, task_id)

    response = client.post(
        "/api/intake/feishu/webhook",
        json={
            "sender_id": "ou_alice",
            "text": f"进度 {task_id} 45 完成了大纲",
            "message_id": "om_m1_p1",
        },
        headers=feishu_headers(),
    )
    assert response.status_code == 200
    body = response.json()
    assert body["intent"] == "progress"
    assert body["progress"] == 45
    assert "45%" in body["reply"]
    assert task_id in body["reply"]
    assert chain_len(factory, task_id) == before + 1

    with factory() as db:
        task = db.get(Task, task_id)
        assert task is not None
        assert task.progress == 45
        newest = max(task.events, key=lambda e: e.seq)
        assert newest.who == "lark-alice"
        assert newest.event_key == "intake:feishu:om_m1_p1"


def test_progress_on_queued_becomes_note(board):
    client, factory = board
    opened = open_hall_card(client, text="尚未开工的卡", message_id="om_m1_pq0")
    task_id = opened["task_id"]
    before = chain_len(factory, task_id)

    response = client.post(
        "/api/intake/feishu/webhook",
        json={
            "sender_id": "ou_alice",
            "text": f"进度 {task_id} 30 想先报一下",
            "message_id": "om_m1_pq1",
        },
        headers=feishu_headers(),
    )
    assert response.status_code == 200
    body = response.json()
    assert body["intent"] == "progress"
    assert "不在进行中" in body["reply"]
    assert chain_len(factory, task_id) == before + 1

    with factory() as db:
        task = db.get(Task, task_id)
        assert task is not None
        assert task.status == "queued"
        assert int(task.progress or 0) == 0


def test_note_appends_chain_only(board):
    client, factory = board
    opened = open_hall_card(client, text="备注目标卡", message_id="om_m1_n0")
    task_id = opened["task_id"]
    before = chain_len(factory, task_id)

    response = client.post(
        "/api/intake/feishu/webhook",
        json={
            "sender_id": "ou_alice",
            "text": f"备注 {task_id} 客户改了范围",
            "message_id": "om_m1_n1",
        },
        headers=feishu_headers(),
    )
    assert response.status_code == 200
    body = response.json()
    assert body["intent"] == "note"
    assert "客户改了范围" in body["reply"]
    assert chain_len(factory, task_id) == before + 1


def test_status_is_read_only(board):
    client, factory = board
    opened = open_hall_card(client, text="查询目标卡", message_id="om_m1_s0")
    task_id = opened["task_id"]
    before = chain_len(factory, task_id)

    response = client.post(
        "/api/intake/feishu/webhook",
        json={
            "sender_id": "ou_alice",
            "text": f"查 {task_id}",
            "message_id": "om_m1_s1",
        },
        headers=feishu_headers(),
    )
    assert response.status_code == 200
    body = response.json()
    assert body["intent"] == "status"
    assert body["status"] == "queued"
    assert body["holder"] == "lark-alice"
    assert task_id in body["reply"]
    assert chain_len(factory, task_id) == before


def test_done_by_holder(board):
    client, factory = board
    opened = open_hall_card(client, text="完成目标卡", message_id="om_m1_d0")
    task_id = opened["task_id"]
    start_as_holder(client, task_id)

    response = client.post(
        "/api/intake/feishu/webhook",
        json={
            "sender_id": "ou_alice",
            "text": f"完成 {task_id} 交付完毕",
            "message_id": "om_m1_d1",
        },
        headers=feishu_headers(),
    )
    assert response.status_code == 200
    body = response.json()
    assert body["intent"] == "done"
    assert body["status"] == "done"
    assert "完成" in body["reply"]
    with factory() as db:
        assert db.get(Task, task_id).status == "done"


def test_done_refused_for_non_holder(board):
    client, factory = board
    opened = open_hall_card(client, text="越权完成卡", message_id="om_m1_r0")
    task_id = opened["task_id"]
    start_as_holder(client, task_id, holder="lark-alice")
    before = chain_len(factory, task_id)

    response = client.post(
        "/api/intake/feishu/webhook",
        json={
            "sender_id": "ou_bob",
            "text": f"完成 {task_id} 我想结案",
            "message_id": "om_m1_r1",
        },
        headers=feishu_headers(),
    )
    assert response.status_code == 200
    body = response.json()
    assert body["intent"] == "done"
    assert body.get("refused") is True
    assert "不是持棒人" in body["reply"]
    assert "500" not in body["reply"]
    assert chain_len(factory, task_id) == before
    with factory() as db:
        assert db.get(Task, task_id).status == "doing"


def test_unmapped_user_zero_writes(board):
    client, factory = board
    with factory() as db:
        before_tasks = db.query(Task).count()
        before_events = db.query(TaskEvent).count()

    response = client.post(
        "/api/intake/feishu/webhook",
        json={
            "sender_id": "ou_stranger",
            "text": "进度 task-20990101-001 10",
            "message_id": "om_m1_u1",
        },
        headers=feishu_headers(),
    )
    assert response.status_code == 403
    assert response.headers.get("x-intake-error") == "channel-user-unmapped"

    with factory() as db:
        assert db.query(Task).count() == before_tasks
        assert db.query(TaskEvent).count() == before_events


def test_write_intents_are_idempotent(board):
    client, factory = board
    opened = open_hall_card(client, text="幂等目标卡", message_id="om_m1_i0")
    task_id = opened["task_id"]
    start_as_holder(client, task_id)

    payload = {
        "sender_id": "ou_alice",
        "text": f"进度 {task_id} 60 半程",
        "message_id": "om_m1_i1",
    }
    first = client.post(
        "/api/intake/feishu/webhook", json=payload, headers=feishu_headers()
    )
    second = client.post(
        "/api/intake/feishu/webhook", json=payload, headers=feishu_headers()
    )
    assert first.status_code == 200
    assert second.status_code == 200
    assert first.json()["reply"] == second.json()["reply"]
    assert first.json()["progress"] == second.json()["progress"] == 60
    # One progress event only (open + start + one progress).
    assert chain_len(factory, task_id) == 3

    note_payload = {
        "sender_id": "ou_alice",
        "text": f"备注 {task_id} 补充一句",
        "message_id": "om_m1_i2",
    }
    n1 = client.post(
        "/api/intake/feishu/webhook", json=note_payload, headers=feishu_headers()
    )
    n2 = client.post(
        "/api/intake/feishu/webhook", json=note_payload, headers=feishu_headers()
    )
    assert n1.json()["reply"] == n2.json()["reply"]
    assert chain_len(factory, task_id) == 4