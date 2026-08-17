"""Feishu inbound bridge M0: simulated events end to end, never off-box.

The bridge pipeline is exercised against the real hub app through
``fastapi.testclient`` (loopback only), and every outward face — Feishu app
API, group webhook, event delivery — is a stub or a local HTTP listener on
127.0.0.1. No test touches the network beyond loopback.
"""

from __future__ import annotations

import json
import threading
import urllib.parse
import urllib.request
from pathlib import Path

import pytest
import yaml
from fastapi.testclient import TestClient

from server.app import create_app
from server.db import (
    Actor,
    ChannelToken,
    ChannelUser,
    make_session_factory,
)
from server.security import hash_token
from tools.feishu_intake_bridge import (
    AppReplier,
    BridgeConfig,
    BridgeError,
    FeishuIntakeBridge,
    NullReplier,
    WebhookReplier,
    load_config,
    make_handler,
    parse_event,
)
from http.server import ThreadingHTTPServer

FEISHU_BEARER = "feishu-channel-bearer"
SERVER_URL = "http://testserver"


@pytest.fixture()
def board(tmp_path):
    factory = make_session_factory(tmp_path / "board.db")
    with factory() as db:
        db.add(Actor(id="lark-alice", kind="human", display_name="Alice"))
        db.flush()
        db.add_all(
            [
                ChannelToken(
                    token_hash=hash_token(FEISHU_BEARER),
                    channel_id="feishu",
                    label="lark bot",
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


class ClientTransport:
    """Routes the bridge's hub calls into the in-process TestClient."""

    def __init__(self, client: TestClient) -> None:
        self.client = client

    def post_json(self, url, body, headers=None, timeout=20):
        path = urllib.parse.urlparse(url).path
        response = self.client.post(path, json=body, headers=headers or {})
        try:
            parsed = response.json()
        except json.JSONDecodeError:
            parsed = None
        return (
            response.status_code,
            {k.lower(): v for k, v in response.headers.items()},
            parsed,
        )


class RecordingReplier:
    def __init__(self) -> None:
        self.sent: list[tuple[str, str]] = []

    def send(self, chat_id: str, text: str) -> None:
        self.sent.append((chat_id, text))


def make_config(**overrides) -> BridgeConfig:
    base = {
        "server_url": SERVER_URL,
        "channel_id": "feishu",
        "channel_token": FEISHU_BEARER,
    }
    base.update(overrides)
    return BridgeConfig(**base)


def make_bridge(board, config=None) -> tuple[FeishuIntakeBridge, RecordingReplier]:
    replier = RecordingReplier()
    bridge = FeishuIntakeBridge(
        config or make_config(),
        intake_transport=ClientTransport(board),
        replier=replier,
    )
    return bridge, replier


def message_event(sender: str, text: str, message_id: str, *, token=None) -> dict:
    return {
        "schema": "2.0",
        "header": {
            "event_type": "im.message.receive_v1",
            "token": token or "vt-test",
        },
        "event": {
            "sender": {"sender_type": "user", "sender_id": {"open_id": sender}},
            "message": {
                "message_id": message_id,
                "chat_id": "oc_chat_1",
                "chat_type": "group",
                "message_type": "text",
                "content": json.dumps({"text": text}, ensure_ascii=False),
            },
        },
    }


# ---------- end to end: message -> card -> receipt ----------


def test_mapped_sender_message_opens_card_and_replies_receipt(board):
    bridge, replier = make_bridge(board)
    result = bridge.handle_event(
        message_event("ou_alice", "@_user_1 整理一份发布会纪要", "om_e2e_1")
    )
    assert result.status == "opened"
    task_id = result.detail["task_id"]

    assert replier.sent
    chat_id, receipt = replier.sent[0]
    assert chat_id == "oc_chat_1"
    assert task_id in receipt

    card = board.get(
        f"/api/tasks/{task_id}",
        headers={"Authorization": f"Bearer {FEISHU_BEARER}"},
    ).json()
    assert card["created_by"] == "lark-alice"
    assert card["open_dispatch"] is True
    assert card["source_channel"] == "feishu"
    assert card["source_user"] == "ou_alice"
    # The bot-mention placeholder never reaches the card face.
    assert "@_user" not in card["title"]
    assert "om_e2e_1" in card["chain"][0]["did"]


def test_redelivered_event_opens_the_same_card(board):
    bridge, replier = make_bridge(board)
    event = message_event("ou_alice", "重复投递", "om_e2e_dup")
    first = bridge.handle_event(event)
    second = bridge.handle_event(event)
    assert first.status == second.status == "opened"
    assert first.detail["task_id"] == second.detail["task_id"]


def test_unmapped_sender_gets_registration_guide_and_no_card(board):
    bridge, replier = make_bridge(board, make_config(registration_guide="请先登记"))
    result = bridge.handle_event(message_event("ou_stranger", "开门", "om_e2e_u1"))
    assert result.status == "guidance"
    assert replier.sent == [("oc_chat_1", "请先登记")]

    listing = board.get(
        "/api/tasks", headers={"Authorization": f"Bearer {FEISHU_BEARER}"}
    )
    assert listing.json() == []


def test_unmapped_refusal_carries_the_machine_readable_marker(board):
    response = board.post(
        "/api/intake/feishu/webhook",
        json={"sender_id": "ou_stranger", "text": "开门", "message_id": "om_m1"},
        headers={"Authorization": f"Bearer {FEISHU_BEARER}"},
    )
    assert response.status_code == 403
    assert response.headers["x-intake-error"] == "channel-user-unmapped"
    assert "ou_stranger" in response.json()["detail"]


# ---------- callback verification and noise ----------


def test_url_verification_challenge_is_echoed(board):
    bridge, _replier = make_bridge(board, make_config(verification_token="vt-test"))
    result = bridge.handle_event(
        {"type": "url_verification", "token": "vt-test", "challenge": "ch-123"}
    )
    assert result.as_dict() == {"status": "challenge", "challenge": "ch-123"}


def test_wrong_verification_token_is_refused(board):
    bridge, _replier = make_bridge(board, make_config(verification_token="vt-test"))
    with pytest.raises(PermissionError):
        bridge.handle_event(
            message_event("ou_alice", "冒充回调", "om_e2e_bad", token="vt-forged")
        )


def test_bot_and_non_text_messages_are_ignored(board):
    bridge, replier = make_bridge(board)
    bot_event = message_event("ou_bot", "机器人自言自语", "om_b1")
    bot_event["event"]["sender"]["sender_type"] = "app"
    assert bridge.handle_event(bot_event).status == "ignored"

    image_event = message_event("ou_alice", "", "om_b2")
    image_event["event"]["message"]["message_type"] = "image"
    image_event["event"]["message"]["content"] = json.dumps({"image_key": "img_v3_x"})
    assert bridge.handle_event(image_event).status == "ignored"
    assert replier.sent == []


def test_parse_event_handles_empty_text_and_missing_ids():
    empty = message_event("ou_alice", "@_user_1", "om_empty")
    assert parse_event(empty).kind == "ignored"


# ---------- event-callback HTTP listener (loopback only) ----------


@pytest.fixture()
def listener(board):
    bridge, replier = make_bridge(board)
    server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(bridge))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_address[1]}", board, replier
    server.shutdown()
    server.server_close()


def _post(url: str, payload: dict) -> tuple[int, dict]:
    request = urllib.request.Request(
        url,
        method="POST",
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=5) as response:
            return response.status, json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read().decode("utf-8"))


def test_http_listener_runs_the_full_pipeline(listener):
    url, board, replier = listener
    status, body = _post(
        url + "/feishu/events", message_event("ou_alice", "回调开卡", "om_http_1")
    )
    assert status == 200
    assert body["status"] == "opened"
    card = board.get(
        f"/api/tasks/{body['task_id']}",
        headers={"Authorization": f"Bearer {FEISHU_BEARER}"},
    ).json()
    assert card["title"] == "回调开卡"
    assert replier.sent and body["task_id"] in replier.sent[0][1]


def test_http_listener_answers_challenges_and_health(listener):
    url, _board, _replier = listener
    status, body = _post(
        url + "/feishu/events", {"type": "url_verification", "challenge": "ch-x"}
    )
    assert status == 200
    assert body["challenge"] == "ch-x"

    with urllib.request.urlopen(url + "/healthz", timeout=5) as response:
        assert response.status == 200


def test_http_listener_rejects_forged_token(board):
    bridge, _replier = make_bridge(board, make_config(verification_token="vt-real"))
    server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(bridge))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        url = f"http://127.0.0.1:{server.server_address[1]}"
        status, _body = _post(
            url + "/feishu/events",
            message_event("ou_alice", "伪造", "om_forged", token="vt-wrong"),
        )
        assert status == 403
    finally:
        server.shutdown()
        server.server_close()


# ---------- receipt channels: credential-gated, stubbed outward ----------


class FakeOutward:
    """Stands in for the Feishu open platform and group webhooks."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict, dict]] = []

    def post_json(self, url, body, headers=None, timeout=20):
        self.calls.append((url, body, headers or {}))
        if "tenant_access_token" in url:
            return 200, {}, {"tenant_access_token": "t-stub-token", "expire": 7200}
        return 200, {}, {"code": 0}


def test_reply_mode_is_credential_gated():
    assert make_config(app_id="cli_x", app_secret="s").reply_mode == "app"
    assert make_config(webhook_url="https://hook.example/x").reply_mode == "webhook"
    assert make_config().reply_mode == "none"
    # App credentials win over the webhook fallback.
    assert (
        make_config(
            app_id="cli_x", app_secret="s", webhook_url="https://hook.example/x"
        ).reply_mode
        == "app"
    )


def test_app_replier_fetches_token_and_addresses_the_chat():
    outward = FakeOutward()
    replier = AppReplier("https://open.feishu.cn", "cli_x", "secret-x", outward)
    replier.send("oc_chat_9", "已开卡 task-1")
    token_call, message_call = outward.calls
    assert "tenant_access_token/internal" in token_call[0]
    assert token_call[1]["app_id"] == "cli_x"
    assert message_call[2]["Authorization"] == "Bearer t-stub-token"
    assert message_call[1]["receive_id"] == "oc_chat_9"
    assert json.loads(message_call[1]["content"])["text"] == "已开卡 task-1"
    # The token is cached: a second send adds exactly one more call.
    replier.send("oc_chat_9", "第二条")
    assert len(outward.calls) == 3


def test_webhook_replier_posts_plain_text():
    outward = FakeOutward()
    replier = WebhookReplier("https://hook.example/group", outward)
    replier.send("oc_chat_1", "已开卡 task-2")
    url, body, _headers = outward.calls[0]
    assert url == "https://hook.example/group"
    assert body == {"msg_type": "text", "content": {"text": "已开卡 task-2"}}


def test_null_replier_keeps_intake_working_when_delivery_degrades(board):
    bridge = FeishuIntakeBridge(
        make_config(),
        intake_transport=ClientTransport(board),
        replier=NullReplier(),
    )
    result = bridge.handle_event(message_event("ou_alice", "降级开卡", "om_deg_1"))
    assert result.status == "opened"


# ---------- deployment config ----------


def test_config_resolves_env_and_file_indirection(tmp_path, monkeypatch):
    token_file = tmp_path / "channel.token"
    token_file.write_text("tok-from-file\n", encoding="utf-8")
    monkeypatch.setenv("K3_TEST_APP_SECRET", "secret-from-env")
    monkeypatch.setenv("K3_TEST_VT", "vt-from-env")
    monkeypatch.setenv("K3_TEST_HOOK", "https://hook.example/from-env")
    config_path = tmp_path / "feishu-bridge.yaml"
    config_path.write_text(
        "\n".join(
            [
                f"server_url: {SERVER_URL}",
                "channel_id: feishu",
                f"channel_token_file: {token_file}",
                "listen:",
                "  host: 127.0.0.1",
                "  port: 9221",
                "feishu:",
                "  app_id: cli_test",
                "  app_secret_env: K3_TEST_APP_SECRET",
                "  verification_token_env: K3_TEST_VT",
                "reply:",
                "  webhook_url_env: K3_TEST_HOOK",
            ]
        ),
        encoding="utf-8",
    )
    config = load_config(config_path)
    assert config.channel_token == "tok-from-file"
    assert config.app_secret == "secret-from-env"
    assert config.verification_token == "vt-from-env"
    assert config.webhook_url == "https://hook.example/from-env"
    assert config.listen_port == 9221
    assert config.reply_mode == "app"


def test_config_refuses_missing_or_dangling_credentials(tmp_path, monkeypatch):
    with pytest.raises(BridgeError):
        load_config(tmp_path / "absent.yaml")

    no_token = tmp_path / "no-token.yaml"
    no_token.write_text(f"server_url: {SERVER_URL}\n", encoding="utf-8")
    with pytest.raises(BridgeError, match="channel_token"):
        load_config(no_token)

    monkeypatch.delenv("K3_TEST_DANGLING", raising=False)
    dangling = tmp_path / "dangling.yaml"
    dangling.write_text(
        f"server_url: {SERVER_URL}\nchannel_token_env: K3_TEST_DANGLING\n",
        encoding="utf-8",
    )
    with pytest.raises(BridgeError, match="unset"):
        load_config(dangling)


def test_repo_config_template_parses():
    template = (
        Path(__file__).resolve().parents[1]
        / "examples"
        / "feishu-bridge.example.yaml"
    )
    raw = yaml.safe_load(template.read_text(encoding="utf-8"))
    assert raw["channel_id"] == "feishu"
    assert raw["feishu"]["app_secret_env"]
    assert raw["reply"]["webhook_url_env"]
