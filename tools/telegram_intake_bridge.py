#!/usr/bin/env python3
"""Telegram inbound bridge: long-poll getUpdates -> hub intake -> reply.

Self-hosted operators can run this without a public callback URL. The bridge
only translates Telegram text updates into the hub intake webhook shape
(``text``, ``sender_id``, ``message_id``) and forwards the hub's Chinese
``reply`` (or the unmapped registration guide) back to the originating chat.
All command parsing stays on the hub; this adapter has zero business logic.

Configuration is a deployment YAML (see ``examples/telegram-bridge.example.yaml``).
The bot token is never written into the file — only the name of an environment
variable that operations injects at deploy time. Channel credentials use the
same ``*_env`` / ``*_file`` indirection as the Feishu bridge.

Docs: ``docs/telegram-bridge-runbook.md``.
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import logging
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from server.http_client import RequestClass, open_url

LOG = logging.getLogger("retinue.telegram-bridge")

TELEGRAM_API_BASE = "https://api.telegram.org"
DEFAULT_REGISTRATION_GUIDE = (
    "你还不能在这里开卡:你的电报身份尚未绑定板上账号。"
    "请把这条消息截图发给板管理员,请其在 channel_users 中登记你的 Telegram 用户 id。"
)
TEXT_LIMIT = 4000
DEFAULT_POLL_TIMEOUT = 25


class BridgeError(RuntimeError):
    """Configuration or transport failure the operator must fix."""


class Transport(Protocol):
    """HTTP JSON helper; tests inject stubs so nothing leaves the machine."""

    def post_json(
        self,
        url: str,
        body: dict[str, Any],
        headers: dict[str, str] | None = None,
        timeout: float = 20,
    ) -> tuple[int, dict[str, str], Any]: ...

    def get_json(
        self,
        url: str,
        headers: dict[str, str] | None = None,
        timeout: float = 40,
    ) -> tuple[int, dict[str, str], Any]: ...


class UrllibTransport:
    """Real HTTP through the shared proxy policy (INWARD hub / OUTWARD Telegram)."""

    def __init__(self, request_class: RequestClass) -> None:
        self.request_class = request_class

    def post_json(
        self,
        url: str,
        body: dict[str, Any],
        headers: dict[str, str] | None = None,
        timeout: float = 20,
    ) -> tuple[int, dict[str, str], Any]:
        request = urllib.request.Request(
            url,
            method="POST",
            data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
            headers={"Content-Type": "application/json", **(headers or {})},
        )
        return self._exchange(request, timeout)

    def get_json(
        self,
        url: str,
        headers: dict[str, str] | None = None,
        timeout: float = 40,
    ) -> tuple[int, dict[str, str], Any]:
        request = urllib.request.Request(
            url, method="GET", headers=dict(headers or {})
        )
        return self._exchange(request, timeout)

    def _exchange(
        self, request: urllib.request.Request, timeout: float
    ) -> tuple[int, dict[str, str], Any]:
        try:
            with open_url(
                request, timeout=timeout, request_class=self.request_class
            ) as response:
                return (
                    response.status,
                    {k.lower(): v for k, v in response.headers.items()},
                    json.loads(response.read().decode("utf-8") or "null"),
                )
        except urllib.error.HTTPError as exc:
            raw = exc.read().decode("utf-8", errors="replace")
            try:
                parsed: Any = json.loads(raw or "null")
            except json.JSONDecodeError:
                parsed = {"detail": raw}
            return (
                exc.code,
                {k.lower(): v for k, v in (exc.headers or {}).items()},
                parsed,
            )
        except urllib.error.URLError as exc:
            raise BridgeError(f"{request.get_method()} {request.full_url}: {exc.reason}") from exc


# ---------- deployment configuration ----------


@dataclass
class BridgeConfig:
    """Resolved runtime settings; secrets arrive already dereferenced."""

    server_url: str
    channel_id: str
    channel_token: str
    bot_token: str
    allowed_chat_ids: frozenset[str] = field(default_factory=frozenset)
    registration_guide: str = DEFAULT_REGISTRATION_GUIDE
    telegram_api_base: str = TELEGRAM_API_BASE
    poll_timeout: int = DEFAULT_POLL_TIMEOUT

    @property
    def bot_api_root(self) -> str:
        return f"{self.telegram_api_base.rstrip('/')}/bot{self.bot_token}"


def _secret(raw: dict[str, Any], field_env: str, field_file: str) -> str | None:
    """Resolve one credential from its env-var or file indirection."""
    env_name = str(raw.get(field_env) or "").strip()
    if env_name:
        value = os.environ.get(env_name, "").strip()
        if not value:
            raise BridgeError(f"config names {field_env}={env_name} but it is unset")
        return value
    file_name = str(raw.get(field_file) or "").strip()
    if file_name:
        path = Path(file_name)
        if not path.is_file():
            raise BridgeError(f"config names {field_file}={file_name}: no such file")
        return path.read_text(encoding="utf-8").strip()
    return None


def load_config(path: Path) -> BridgeConfig:
    """Load the deployment YAML and dereference every credential indirection."""
    if not path.is_file():
        raise BridgeError(f"missing config file: {path}")
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as exc:
        raise BridgeError(f"invalid YAML in {path}: {exc}") from exc
    if not isinstance(raw, dict):
        raise BridgeError(f"{path} must contain a mapping at the top level")

    telegram = raw.get("telegram") or {}
    if not isinstance(telegram, dict):
        raise BridgeError("config telegram: must be a mapping")

    token = _secret(raw, "channel_token_env", "channel_token_file")
    if not token:
        raise BridgeError(
            "config needs channel_token_env or channel_token_file: "
            "the bridge cannot call intake without its channel credential"
        )
    bot_token = _secret(telegram, "bot_token_env", "bot_token_file")
    if not bot_token:
        raise BridgeError(
            "config needs telegram.bot_token_env (preferred) or telegram.bot_token_file: "
            "never put the bot token itself in the YAML"
        )
    server_url = str(raw.get("server_url") or "").strip()
    if not server_url:
        raise BridgeError("config needs server_url")

    allowed_raw = telegram.get("allowed_chat_ids") or raw.get("allowed_chat_ids") or []
    if allowed_raw and not isinstance(allowed_raw, list):
        raise BridgeError("allowed_chat_ids must be a list of chat ids")
    allowed = frozenset(str(item).strip() for item in allowed_raw if str(item).strip())

    poll_timeout = int(telegram.get("poll_timeout") or DEFAULT_POLL_TIMEOUT)
    if poll_timeout < 0 or poll_timeout > 50:
        raise BridgeError("telegram.poll_timeout must be between 0 and 50")

    return BridgeConfig(
        server_url=server_url.rstrip("/"),
        channel_id=str(raw.get("channel_id") or "telegram").strip() or "telegram",
        channel_token=token,
        bot_token=bot_token,
        allowed_chat_ids=allowed,
        registration_guide=str(
            raw.get("registration_guide") or DEFAULT_REGISTRATION_GUIDE
        ).strip(),
        telegram_api_base=str(telegram.get("api_base") or TELEGRAM_API_BASE).rstrip(
            "/"
        ),
        poll_timeout=poll_timeout,
    )


# ---------- Telegram update normalization ----------


@dataclass
class InboundMessage:
    sender_id: str
    text: str
    message_id: str
    chat_id: str


def normalize_update(update: dict[str, Any]) -> InboundMessage | None:
    """Reduce one getUpdates payload to hub intake fields, or None if ignored.

    ``message_id`` uses Telegram ``update_id`` so a crash-restart that re-polls
    the same update stays idempotent on the hub
    (``event_key = intake:{channel}:{message_id}``). ``sender_id`` is the
    numeric Telegram user id as a decimal string.
    """
    if not isinstance(update, dict):
        return None
    update_id = update.get("update_id")
    if update_id is None:
        return None
    message = update.get("message") or update.get("edited_message")
    if not isinstance(message, dict):
        return None
    if message.get("text") is None:
        return None
    text = str(message.get("text") or "").strip()
    if not text:
        return None
    text = text[:TEXT_LIMIT]
    from_user = message.get("from") or {}
    if not isinstance(from_user, dict) or from_user.get("is_bot"):
        return None
    user_id = from_user.get("id")
    if user_id is None:
        return None
    chat = message.get("chat") or {}
    if not isinstance(chat, dict) or chat.get("id") is None:
        return None
    return InboundMessage(
        sender_id=str(user_id),
        text=text,
        message_id=str(update_id),
        chat_id=str(chat["id"]),
    )


def chat_allowed(config: BridgeConfig, chat_id: str) -> bool:
    """Empty whitelist means all chats; otherwise require an exact id match."""
    if not config.allowed_chat_ids:
        return True
    return chat_id in config.allowed_chat_ids


# ---------- reply delivery ----------


class Replier(Protocol):
    def send(self, chat_id: str, text: str) -> None: ...


class TelegramReplier:
    """sendMessage back to the originating chat via Bot API."""

    def __init__(self, api_root: str, transport: Transport) -> None:
        self.api_root = api_root
        self.transport = transport

    def send(self, chat_id: str, text: str) -> None:
        status, _headers, body = self.transport.post_json(
            f"{self.api_root}/sendMessage",
            {"chat_id": chat_id, "text": text},
            timeout=15,
        )
        ok = body.get("ok") if isinstance(body, dict) else None
        if status >= 400 or ok is False:
            raise BridgeError(f"Telegram sendMessage failed: HTTP {status} {body}")


class NullReplier:
    def send(self, chat_id: str, text: str) -> None:
        LOG.warning(
            "no reply channel; receipt for chat %s stays undelivered: %s",
            chat_id,
            text,
        )


# ---------- the bridge pipeline ----------


@dataclass
class UpdateResult:
    status: str  # "replied" | "guidance" | "ignored" | "error"
    detail: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {"status": self.status, **self.detail}


def extract_reply_text(body: Any) -> str | None:
    """Prefer the hub M1 ``reply`` field; otherwise None (no adapter-side copy)."""
    if not isinstance(body, dict):
        return None
    reply = body.get("reply")
    if isinstance(reply, str) and reply.strip():
        return reply
    return None


class TelegramIntakeBridge:
    """Telegram update in, hub intake out, Chinese reply back to chat."""

    def __init__(
        self,
        config: BridgeConfig,
        *,
        intake_transport: Transport | None = None,
        outward_transport: Transport | None = None,
        replier: Replier | None = None,
    ) -> None:
        self.config = config
        self.intake = intake_transport or UrllibTransport(RequestClass.INWARD)
        self.outward = outward_transport or UrllibTransport(RequestClass.OUTWARD)
        self.replier = replier or TelegramReplier(config.bot_api_root, self.outward)

    def forward_intake(
        self, message: InboundMessage
    ) -> tuple[int, dict[str, str], Any]:
        return self.intake.post_json(
            f"{self.config.server_url}/api/intake/{self.config.channel_id}/webhook",
            {
                "sender_id": message.sender_id,
                "text": message.text,
                "message_id": message.message_id,
            },
            headers={"Authorization": f"Bearer {self.config.channel_token}"},
        )

    def handle_update(self, update: dict[str, Any]) -> UpdateResult:
        message = normalize_update(update)
        if message is None:
            return UpdateResult("ignored", {"reason": "not a text user message"})
        if not chat_allowed(self.config, message.chat_id):
            LOG.info(
                "chat %s not in allowed_chat_ids; dropping update %s",
                message.chat_id,
                message.message_id,
            )
            return UpdateResult(
                "ignored",
                {"reason": "chat not allowlisted", "chat_id": message.chat_id},
            )
        status, headers, body = self.forward_intake(message)
        if status == 200:
            reply = extract_reply_text(body)
            if reply is None:
                LOG.error(
                    "intake 200 without reply for message %s: %s",
                    message.message_id,
                    body,
                )
                return UpdateResult(
                    "error",
                    {"http_status": status, "body": body, "reason": "missing reply"},
                )
            self.replier.send(message.chat_id, reply)
            LOG.info(
                "relayed reply for %s (sender %s)",
                message.message_id,
                message.sender_id,
            )
            return UpdateResult(
                "replied",
                {
                    "message_id": message.message_id,
                    "reply": reply,
                    "intent": body.get("intent") if isinstance(body, dict) else None,
                },
            )
        if status == 403 and headers.get("x-intake-error") == "channel-user-unmapped":
            self.replier.send(message.chat_id, self.config.registration_guide)
            LOG.info("unmapped sender %s; registration guide sent", message.sender_id)
            return UpdateResult("guidance", {"sender_id": message.sender_id})
        LOG.error(
            "intake refused message %s: HTTP %s %s", message.message_id, status, body
        )
        return UpdateResult("error", {"http_status": status, "body": body})


# ---------- long polling ----------


def fetch_updates(
    config: BridgeConfig,
    transport: Transport,
    *,
    offset: int | None,
) -> list[dict[str, Any]]:
    """One getUpdates long-poll; returns the ``result`` list (may be empty)."""
    query: dict[str, Any] = {"timeout": config.poll_timeout}
    if offset is not None:
        query["offset"] = offset
    url = f"{config.bot_api_root}/getUpdates?{urllib.parse.urlencode(query)}"
    # Long-poll: allow Telegram timeout plus a small network margin.
    status, _headers, body = transport.get_json(
        url, timeout=float(config.poll_timeout) + 15
    )
    if status >= 400 or not isinstance(body, dict) or not body.get("ok"):
        raise BridgeError(f"getUpdates failed: HTTP {status} {body}")
    result = body.get("result") or []
    if not isinstance(result, list):
        raise BridgeError(f"getUpdates result is not a list: {body}")
    return [item for item in result if isinstance(item, dict)]


def poll_forever(bridge: TelegramIntakeBridge) -> None:
    """Acknowledge updates via offset so Telegram does not redeliver forever."""
    offset: int | None = None
    LOG.info(
        "telegram intake bridge long-polling getUpdates (poll_timeout=%ss, whitelist=%s)",
        bridge.config.poll_timeout,
        "on" if bridge.config.allowed_chat_ids else "off",
    )
    while True:
        try:
            updates = fetch_updates(
                bridge.config, bridge.outward, offset=offset
            )
        except BridgeError as exc:
            LOG.error("poll failed: %s; retrying in 3s", exc)
            time.sleep(3)
            continue
        for update in updates:
            update_id = update.get("update_id")
            if isinstance(update_id, int):
                offset = update_id + 1
            try:
                result = bridge.handle_update(update)
                if result.status == "error":
                    LOG.error("update handling error: %s", result.as_dict())
            except BridgeError as exc:
                LOG.error("update handling failed: %s", exc)


# ---------- CLI ----------


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--config", required=True, help="deployment telegram-bridge.yaml"
    )
    parser.add_argument(
        "--check-config",
        action="store_true",
        help="resolve the config, print the non-secret summary, and exit",
    )
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    try:
        config = load_config(Path(args.config))
    except BridgeError as exc:
        LOG.error("%s", exc)
        return 2
    if args.check_config:
        summary = dataclasses.asdict(config)
        summary["channel_token"] = "***"
        summary["bot_token"] = "***"
        summary["allowed_chat_ids"] = sorted(config.allowed_chat_ids)
        # bot_api_root embeds the token; never print it.
        summary["bot_api_configured"] = True
        print(json.dumps(summary, ensure_ascii=False, indent=2))
        return 0
    bridge = TelegramIntakeBridge(config)
    try:
        poll_forever(bridge)
    except KeyboardInterrupt:
        LOG.info("stopping")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
