"""Intake protocol M0 (schema v16): channel tokens, intake webhook, enroll.

Layer 1 — channel identity: a channel credential can only open cards and
read the cards its own channel opened; it can never claim a card, write
someone else's card, or act as an executor.
Layer 2 — publish specification: channel cards go through the existing
create route (or the generic webhook adapter) as open dispatch, signed by
the mapped board user, with the original message digest in the first note.
Layer 3 — executor self-registration: the enroll handshake records an
application; only an admin decision creates the actor and shows the token.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from server.app import create_app
from server.db import (
    Actor,
    ApiToken,
    ChannelToken,
    ChannelUser,
    User,
    make_session_factory,
)
from server.security import hash_password, hash_token

FEISHU_BEARER = "feishu-channel-bearer"
DESKTOP_BEARER = "desktop-channel-bearer"
AGENT_BEARER = "plain-agent-bearer"


@pytest.fixture()
def board(tmp_path):
    factory = make_session_factory(tmp_path / "board.db")
    with factory() as db:
        db.add_all(
            [
                Actor(id="lark-alice", kind="human", display_name="Alice"),
                Actor(id="desk-queen", kind="human", display_name="Queen"),
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
                User(
                    username="member",
                    password_hash=hash_password("member-pass-1"),
                    role="member",
                    actor_id="desk-queen",
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
        db.add_all(
            [
                ChannelToken(
                    token_hash=hash_token(FEISHU_BEARER),
                    channel_id="feishu",
                    label="lark bot",
                ),
                ChannelToken(
                    token_hash=hash_token(DESKTOP_BEARER),
                    channel_id="desktop",
                    label="desktop cursor",
                ),
                ChannelUser(
                    channel_id="feishu",
                    channel_user_id="ou_alice",
                    actor_id="lark-alice",
                    display_name="Alice",
                ),
            ]
        )
        db.commit()
    return TestClient(create_app(factory))


def feishu_headers() -> dict[str, str]:
    return {"Authorization": f"Bearer {FEISHU_BEARER}"}


def desktop_headers() -> dict[str, str]:
    return {"Authorization": f"Bearer {DESKTOP_BEARER}"}


def admin_login(board) -> None:
    response = board.post(
        "/api/auth/login", json={"username": "admin", "password": "admin-pass-1"}
    )
    assert response.status_code == 200


# ---------- layer 2: the webhook adapter opens signed hall cards ----------


def test_webhook_opens_hall_card_signed_by_mapped_user(board):
    response = board.post(
        "/api/intake/feishu/webhook",
        json={
            "sender_id": "ou_alice",
            "text": "整理一份发布会纪要\n第二条:尽快",
            "message_id": "om_x100",
        },
        headers=feishu_headers(),
    )
    assert response.status_code == 200
    body = response.json()
    assert body["created_by"] == "lark-alice"

    card = board.get(f"/api/tasks/{body['task_id']}", headers=feishu_headers())
    assert card.status_code == 200
    data = card.json()
    assert data["open_dispatch"] is True
    assert data["holder"] == "lark-alice"
    assert data["source_channel"] == "feishu"
    assert data["source_user"] == "ou_alice"
    first = data["chain"][0]
    assert "om_x100" in first["did"]
    assert "整理一份发布会纪要" in first["did"]
    assert first["payload"]["source_channel"] == "feishu"
    assert first["payload"]["source_user"] == "ou_alice"


def test_webhook_is_idempotent_per_message(board):
    payload = {
        "sender_id": "ou_alice",
        "text": "重复投递的消息",
        "message_id": "om_dup",
    }
    first = board.post(
        "/api/intake/feishu/webhook", json=payload, headers=feishu_headers()
    )
    second = board.post(
        "/api/intake/feishu/webhook", json=payload, headers=feishu_headers()
    )
    assert first.status_code == 200
    assert second.status_code == 200
    assert first.json()["task_id"] == second.json()["task_id"]


def test_webhook_refuses_unmapped_sender(board):
    response = board.post(
        "/api/intake/feishu/webhook",
        json={"sender_id": "ou_stranger", "text": "开门", "message_id": "om_x1"},
        headers=feishu_headers(),
    )
    assert response.status_code == 403
    assert "ou_stranger" in response.json()["detail"]


def test_webhook_requires_matching_channel_credential(board):
    wrong_channel = board.post(
        "/api/intake/desktop/webhook",
        json={"sender_id": "ou_alice", "text": "串门", "message_id": "om_x2"},
        headers=feishu_headers(),
    )
    assert wrong_channel.status_code == 403

    agent_credential = board.post(
        "/api/intake/feishu/webhook",
        json={"sender_id": "ou_alice", "text": "冒充", "message_id": "om_x3"},
        headers={"Authorization": f"Bearer {AGENT_BEARER}"},
    )
    assert agent_credential.status_code == 403

    anonymous = board.post(
        "/api/intake/feishu/webhook",
        json={"sender_id": "ou_alice", "text": "匿名", "message_id": "om_x4"},
    )
    assert anonymous.status_code == 401


# ---------- layer 1: channel token capabilities and denials ----------


def test_channel_token_opens_card_via_create_route(board):
    response = board.post(
        "/api/tasks",
        json={
            "title": "桌面发来的任务",
            "open_dispatch": True,
            "source_user": "ou_alice",
            "note": "通道开卡 [feishu] 用户 ou_alice: 桌面发来的任务",
        },
        headers=feishu_headers(),
    )
    assert response.status_code == 200
    data = response.json()
    assert data["created_by"] == "lark-alice"
    assert data["holder"] == "lark-alice"
    assert data["open_dispatch"] is True
    assert data["source_channel"] == "feishu"
    assert data["source_user"] == "ou_alice"


def test_channel_token_create_requires_open_dispatch_and_source(board):
    no_dispatch = board.post(
        "/api/tasks",
        json={"title": "不挂单", "source_user": "ou_alice"},
        headers=feishu_headers(),
    )
    assert no_dispatch.status_code == 422

    no_source = board.post(
        "/api/tasks",
        json={"title": "无来源", "open_dispatch": True},
        headers=feishu_headers(),
    )
    assert no_source.status_code == 422

    unknown_source = board.post(
        "/api/tasks",
        json={
            "title": "陌生来源",
            "open_dispatch": True,
            "source_user": "ou_stranger",
        },
        headers=feishu_headers(),
    )
    assert unknown_source.status_code == 403


def test_channel_token_cannot_name_an_executor(board):
    response = board.post(
        "/api/tasks",
        json={
            "title": "指派执行者",
            "open_dispatch": True,
            "holder": "agent-x",
            "source_user": "ou_alice",
        },
        headers=feishu_headers(),
    )
    assert response.status_code == 403


def test_source_user_is_channel_only(board):
    forged = board.post(
        "/api/tasks",
        json={
            "title": "伪造来源",
            "holder": "agent-x",
            "source_user": "ou_alice",
        },
        headers={"Authorization": f"Bearer {AGENT_BEARER}"},
    )
    assert forged.status_code == 422


def test_channel_token_cannot_claim_or_write_cards(board):
    opened = board.post(
        "/api/intake/feishu/webhook",
        json={"sender_id": "ou_alice", "text": "待接的卡", "message_id": "om_c1"},
        headers=feishu_headers(),
    )
    task_id = opened.json()["task_id"]

    claim = board.post(
        f"/api/tasks/{task_id}/claim", json={}, headers=feishu_headers()
    )
    assert claim.status_code == 403

    update = board.post(
        f"/api/tasks/{task_id}/update",
        json={"progress": 50},
        headers=feishu_headers(),
    )
    assert update.status_code == 403

    dispatch = board.post(
        "/api/dispatch", json={"intent": "x"}, headers=feishu_headers()
    )
    assert dispatch.status_code == 403

    admin_route = board.get("/api/admin/channel-tokens", headers=feishu_headers())
    assert admin_route.status_code == 403


def test_channel_token_reads_only_its_own_cards(board):
    own = board.post(
        "/api/intake/feishu/webhook",
        json={"sender_id": "ou_alice", "text": "飞书开的卡", "message_id": "om_r1"},
        headers=feishu_headers(),
    )
    own_id = own.json()["task_id"]

    admin_login(board)
    other = board.post(
        "/api/tasks", json={"title": "操作员开的卡", "holder": "desk-queen"}
    )
    assert other.status_code == 200
    other_id = other.json()["id"]

    listing = board.get("/api/tasks", headers=feishu_headers())
    assert listing.status_code == 200
    listed_ids = {item["id"] for item in listing.json()}
    assert own_id in listed_ids
    assert other_id not in listed_ids

    assert board.get(f"/api/tasks/{own_id}", headers=feishu_headers()).status_code == 200
    denied = board.get(f"/api/tasks/{other_id}", headers=feishu_headers())
    assert denied.status_code == 403

    # Another channel does not see feishu's cards either.
    assert board.get(f"/api/tasks/{own_id}", headers=desktop_headers()).status_code == 403


def test_disabled_channel_token_stops_authenticating(board, tmp_path):
    admin_login(board)
    tokens = board.get("/api/admin/channel-tokens")
    token_id = next(t["id"] for t in tokens.json() if t["channel_id"] == "feishu")
    revoked = board.post(f"/api/admin/channel-tokens/{token_id}/revoke")
    assert revoked.status_code == 200

    response = board.post(
        "/api/intake/feishu/webhook",
        json={"sender_id": "ou_alice", "text": "吊销后", "message_id": "om_z1"},
        headers=feishu_headers(),
    )
    assert response.status_code == 401


# ---------- admin management of channels and mappings ----------


def test_admin_manages_channel_tokens_and_mappings(board):
    admin_login(board)
    created = board.post(
        "/api/admin/channel-tokens",
        json={"channel_id": "webhook-x", "label": "generic"},
    )
    assert created.status_code == 200
    assert created.json()["token"]

    mapped = board.put(
        "/api/admin/channel-users",
        json={
            "channel_id": "feishu",
            "channel_user_id": "ou_bob",
            "actor_id": "desk-queen",
            "display_name": "Bob",
        },
    )
    assert mapped.status_code == 200

    listing = board.get("/api/admin/channel-users", params={"channel_id": "feishu"})
    assert {m["channel_user_id"] for m in listing.json()} == {"ou_alice", "ou_bob"}

    mapping_id = next(
        m["id"] for m in listing.json() if m["channel_user_id"] == "ou_bob"
    )
    deleted = board.delete(f"/api/admin/channel-users/{mapping_id}")
    assert deleted.status_code == 200

    unknown_actor = board.put(
        "/api/admin/channel-users",
        json={
            "channel_id": "feishu",
            "channel_user_id": "ou_carol",
            "actor_id": "nobody",
        },
    )
    assert unknown_actor.status_code == 422


def test_channel_admin_routes_reject_non_admin(board):
    anonymous = board.get("/api/admin/channel-tokens")
    assert anonymous.status_code == 401

    member = board.post(
        "/api/auth/login", json={"username": "member", "password": "member-pass-1"}
    )
    assert member.status_code == 200
    assert board.get("/api/admin/channel-tokens").status_code == 403
    assert (
        board.post(
            "/api/admin/channel-tokens", json={"channel_id": "sneaky"}
        ).status_code
        == 403
    )


# ---------- layer 3: executor self-registration ----------


def _enroll(board, **overrides):
    payload = {
        "fingerprint": "node-fp-0001",
        "requested_actor_id": "newbie",
        "display_name": "Newbie",
        "runtime": "claude-code",
        "model": "opus",
        "node_id": "node-a",
        "capabilities": ["python", "review"],
    }
    payload.update(overrides)
    return board.post("/api/enroll", json=payload)


def test_enroll_handshake_records_pending_application(board):
    response = _enroll(board)
    assert response.status_code == 200
    assert response.json()["status"] == "pending"

    # Pending means no actor and no credential: the board stays unwritable.
    admin_login(board)
    actors = board.get("/api/actors")
    assert "newbie" not in {a["id"] for a in actors.json()}

    duplicate = _enroll(board)
    assert duplicate.status_code == 409


def test_enroll_rejects_taken_actor_id(board):
    response = _enroll(board, fingerprint="node-fp-0002", requested_actor_id="agent-x")
    assert response.status_code == 409


def test_enroll_decision_requires_admin(board):
    application_id = _enroll(board).json()["application_id"]

    anonymous = board.post(
        f"/api/admin/enroll-applications/{application_id}/decide",
        json={"decision": "approve"},
    )
    assert anonymous.status_code == 401

    board.post(
        "/api/auth/login", json={"username": "member", "password": "member-pass-1"}
    )
    member = board.post(
        f"/api/admin/enroll-applications/{application_id}/decide",
        json={"decision": "approve"},
    )
    assert member.status_code == 403


def test_enroll_approval_issues_a_working_executor_token(board):
    application_id = _enroll(board).json()["application_id"]
    admin_login(board)

    decided = board.post(
        f"/api/admin/enroll-applications/{application_id}/decide",
        json={"decision": "approve", "note": "roster 已批"},
    )
    assert decided.status_code == 200
    token = decided.json()["token"]
    assert token
    assert decided.json()["application"]["status"] == "approved"
    assert decided.json()["application"]["decided_by"] == "admin-op"

    headers = {"Authorization": f"Bearer {token}"}
    own_card = board.post(
        "/api/tasks", json={"title": "新执行者自己的卡", "holder": "newbie"},
        headers=headers,
    )
    assert own_card.status_code == 200
    assert own_card.json()["holder"] == "newbie"

    # The decision is final: no second token for the same application.
    again = board.post(
        f"/api/admin/enroll-applications/{application_id}/decide",
        json={"decision": "approve"},
    )
    assert again.status_code == 409


def test_enroll_rejection_grants_nothing(board):
    application_id = _enroll(
        board, fingerprint="node-fp-0009", requested_actor_id="rejected-one"
    ).json()["application_id"]
    admin_login(board)
    decided = board.post(
        f"/api/admin/enroll-applications/{application_id}/decide",
        json={"decision": "reject", "note": "指纹存疑"},
    )
    assert decided.status_code == 200
    assert decided.json()["application"]["status"] == "rejected"
    assert decided.json()["token"] is None

    actors = board.get("/api/actors")
    assert "rejected-one" not in {a["id"] for a in actors.json()}

    # A rejected fingerprint may re-apply later.
    retry = _enroll(board, fingerprint="node-fp-0009", requested_actor_id="retry-one")
    assert retry.status_code == 200
