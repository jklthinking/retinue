"""Telegram inbound bridge: pure normalization + stubbed HTTP, never off-box."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
import yaml

from tools.telegram_intake_bridge import (
    BridgeConfig,
    BridgeError,
    InboundMessage,
    TelegramIntakeBridge,
    chat_allowed,
    extract_reply_text,
    load_config,
    main,
    normalize_update,
)


class StubTransport:
    """Records outbound calls and returns scripted responses."""

    def __init__(self) -> None:
        self.posts: list[tuple[str, dict[str, Any], dict[str, str] | None]] = []
        self.gets: list[str] = []
        self._post_queue: list[tuple[int, dict[str, str], Any]] = []
        self._get_queue: list[tuple[int, dict[str, str], Any]] = []

    def queue_post(self, status: int, body: Any, headers: dict[str, str] | None = None) -> None:
        self._post_queue.append((status, {k.lower(): v for k, v in (headers or {}).items()}, body))

    def queue_get(self, status: int, body: Any, headers: dict[str, str] | None = None) -> None:
        self._get_queue.append((status, {k.lower(): v for k, v in (headers or {}).items()}, body))

    def post_json(self, url, body, headers=None, timeout=20):
        self.posts.append((url, body, headers))
        if not self._post_queue:
            raise AssertionError(f"unexpected POST {url}")
        return self._post_queue.pop(0)

    def get_json(self, url, headers=None, timeout=40):
        self.gets.append(url)
        if not self._get_queue:
            raise AssertionError(f"unexpected GET {url}")
        return self._get_queue.pop(0)


class RecordingReplier:
    def __init__(self) -> None:
        self.sent: list[tuple[str, str]] = []

    def send(self, chat_id: str, text: str) -> None:
        self.sent.append((chat_id, text))


def make_config(**overrides) -> BridgeConfig:
    base = {
        "server_url": "http://127.0.0.1:9219",
        "channel_id": "telegram",
        "channel_token": "channel-bearer-test",
        "bot_token": "bot-token-test",
    }
    base.update(overrides)
    return BridgeConfig(**base)


def text_update(
    *,
    update_id: int = 1001,
    user_id: int = 4242,
    chat_id: int = -100555,
    text: str = "开卡 写一份周报",
    is_bot: bool = False,
    edited: bool = False,
) -> dict[str, Any]:
    message = {
        "message_id": 77,
        "from": {"id": user_id, "is_bot": is_bot, "first_name": "Ada"},
        "chat": {"id": chat_id, "type": "private"},
        "text": text,
    }
    key = "edited_message" if edited else "message"
    return {"update_id": update_id, key: message}


# ---------- normalize_update ----------


def test_normalize_text_message_uses_update_id_and_numeric_user():
    inbound = normalize_update(text_update(update_id=9001, user_id=12345, text="  进度 task-1 50  "))
    assert inbound == InboundMessage(
        sender_id="12345",
        text="进度 task-1 50",
        message_id="9001",
        chat_id="-100555",
    )


def test_normalize_ignores_non_text_bots_and_noise():
    assert normalize_update({"update_id": 1}) is None
    assert normalize_update({"update_id": 2, "callback_query": {}}) is None
    assert (
        normalize_update(
            {
                "update_id": 3,
                "message": {
                    "message_id": 1,
                    "from": {"id": 1, "is_bot": False},
                    "chat": {"id": 9},
                    "photo": [],
                },
            }
        )
        is None
    )
    assert normalize_update(text_update(is_bot=True)) is None
    assert normalize_update(text_update(text="   ")) is None


def test_normalize_accepts_edited_message():
    inbound = normalize_update(text_update(text="备注 task-1 补一句", edited=True))
    assert inbound is not None
    assert inbound.text == "备注 task-1 补一句"


# ---------- whitelist ----------


def test_chat_whitelist_empty_allows_all():
    config = make_config()
    assert chat_allowed(config, "-100555")
    assert chat_allowed(config, "1")


def test_chat_whitelist_filters_unknown_chats():
    config = make_config(allowed_chat_ids=frozenset({"-100555", "42"}))
    assert chat_allowed(config, "-100555")
    assert not chat_allowed(config, "999")


# ---------- reply / guidance forwarding ----------


def test_handle_update_forwards_hub_reply_verbatim():
    intake = StubTransport()
    intake.queue_post(
        200,
        {"intent": "open", "task_id": "task-20260814-001", "reply": "已开卡 task-20260814-001"},
    )
    replier = RecordingReplier()
    bridge = TelegramIntakeBridge(
        make_config(), intake_transport=intake, replier=replier
    )
    result = bridge.handle_update(text_update(text="整理纪要"))
    assert result.status == "replied"
    assert result.detail["reply"] == "已开卡 task-20260814-001"
    assert replier.sent == [("-100555", "已开卡 task-20260814-001")]
    url, body, headers = intake.posts[0]
    assert url.endswith("/api/intake/telegram/webhook")
    assert body == {
        "sender_id": "4242",
        "text": "整理纪要",
        "message_id": "1001",
    }
    assert headers["Authorization"] == "Bearer channel-bearer-test"


def test_unmapped_sender_gets_registration_guide():
    intake = StubTransport()
    intake.queue_post(
        403,
        {"detail": "channel user 999 not mapped"},
        headers={"X-Intake-Error": "channel-user-unmapped"},
    )
    replier = RecordingReplier()
    bridge = TelegramIntakeBridge(
        make_config(registration_guide="请先登记电报身份"),
        intake_transport=intake,
        replier=replier,
    )
    result = bridge.handle_update(text_update(user_id=999, text="开门"))
    assert result.status == "guidance"
    assert replier.sent == [("-100555", "请先登记电报身份")]


def test_whitelist_drop_never_calls_intake():
    intake = StubTransport()
    replier = RecordingReplier()
    bridge = TelegramIntakeBridge(
        make_config(allowed_chat_ids=frozenset({"42"})),
        intake_transport=intake,
        replier=replier,
    )
    result = bridge.handle_update(text_update(chat_id=-100555))
    assert result.status == "ignored"
    assert result.detail["reason"] == "chat not allowlisted"
    assert intake.posts == []
    assert replier.sent == []


def test_extract_reply_text_prefers_reply_field():
    assert extract_reply_text({"reply": "进度已记 40%"}) == "进度已记 40%"
    assert extract_reply_text({"task_id": "task-1", "receipt": "旧文案"}) is None
    assert extract_reply_text(None) is None
    assert extract_reply_text({"reply": "  "}) is None


def test_missing_reply_on_200_is_error_without_fallback_copy():
    intake = StubTransport()
    intake.queue_post(200, {"task_id": "task-1", "status": "todo"})
    replier = RecordingReplier()
    bridge = TelegramIntakeBridge(
        make_config(), intake_transport=intake, replier=replier
    )
    result = bridge.handle_update(text_update())
    assert result.status == "error"
    assert result.detail["reason"] == "missing reply"
    assert replier.sent == []


# ---------- config / check-config ----------


def test_load_config_resolves_env_secrets(tmp_path, monkeypatch):
    token_file = tmp_path / "channel.token"
    token_file.write_text("channel-from-file\n", encoding="utf-8")
    monkeypatch.setenv("RETINUE_TG_BOT_TOKEN", "bot-from-env")
    path = tmp_path / "telegram-bridge.yaml"
    path.write_text(
        yaml.safe_dump(
            {
                "server_url": "http://127.0.0.1:9219/",
                "channel_id": "telegram",
                "channel_token_file": str(token_file),
                "telegram": {
                    "bot_token_env": "RETINUE_TG_BOT_TOKEN",
                    "allowed_chat_ids": [-1001, "42"],
                    "poll_timeout": 10,
                },
                "registration_guide": "登记引导",
            }
        ),
        encoding="utf-8",
    )
    config = load_config(path)
    assert config.server_url == "http://127.0.0.1:9219"
    assert config.channel_token == "channel-from-file"
    assert config.bot_token == "bot-from-env"
    assert config.allowed_chat_ids == frozenset({"-1001", "42"})
    assert config.poll_timeout == 10
    assert config.registration_guide == "登记引导"
    assert "bot-from-env" in config.bot_api_root


def test_load_config_rejects_missing_bot_token_indirection(tmp_path):
    token_file = tmp_path / "channel.token"
    token_file.write_text("channel-ok", encoding="utf-8")
    path = tmp_path / "bad.yaml"
    path.write_text(
        yaml.safe_dump(
            {
                "server_url": "http://127.0.0.1:9219",
                "channel_token_file": str(token_file),
                "telegram": {},
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(BridgeError, match="bot_token_env"):
        load_config(path)


def test_check_config_prints_redacted_summary(tmp_path, monkeypatch, capsys):
    token_file = tmp_path / "channel.token"
    token_file.write_text("secret-channel", encoding="utf-8")
    monkeypatch.setenv("RETINUE_TG_BOT_TOKEN", "secret-bot")
    path = tmp_path / "telegram-bridge.yaml"
    path.write_text(
        yaml.safe_dump(
            {
                "server_url": "http://127.0.0.1:9219",
                "channel_id": "telegram",
                "channel_token_file": str(token_file),
                "telegram": {"bot_token_env": "RETINUE_TG_BOT_TOKEN"},
            }
        ),
        encoding="utf-8",
    )
    assert main(["--config", str(path), "--check-config"]) == 0
    out = capsys.readouterr().out
    printed = json.loads(out)
    assert printed["channel_token"] == "***"
    assert printed["bot_token"] == "***"
    assert "secret-bot" not in out
    assert printed["bot_api_configured"] is True
