#!/usr/bin/env python3
"""Feishu inbound bridge M0: user message -> hall card -> receipt reply.

Two event sources feed the same pipeline:

- **event-callback HTTP mode**: a local listener for Feishu event-subscription
  callbacks, meant to sit behind a reverse proxy or tunnel that the Feishu
  console points at;
- **simulated event injection** (``--simulate``): event payloads read from
  JSON files or stdin, for offline end-to-end tests that never leave the
  machine.

Every inbound text message is normalized and posted to the hub's intake
webhook (``POST /api/intake/{channel}/webhook``) with the channel token. A
mapped sender gets a card-number receipt; an unmapped sender gets the
registration guide. Replies go through the app message API when app
credentials are configured, else through a group custom-bot webhook, else the
bridge degrades to log-only delivery and says so at startup.

All settings come from a deployment config (``feishu-bridge.yaml`` in the
data directory); the repository carries only a placeholder template
(``examples/feishu-bridge.example.yaml``). Credentials are never written
into the repository: every secret field is an ``*_env`` / ``*_file``
indirection and operations injects the values at deploy time. Inbound
availability needs a Feishu application with event subscription; a group
webhook alone can only deliver, never receive — see
``docs/feishu-bridge-runbook.md``.
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import logging
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Protocol

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from server.http_client import RequestClass, open_url

LOG = logging.getLogger("retinue.feishu-bridge")

FEISHU_API_BASE = "https://open.feishu.cn"
DEFAULT_REGISTRATION_GUIDE = (
    "你还不能在这里开卡:你的飞书身份尚未绑定板上账号。"
    "请把这条消息截图发给板管理员,请其在 channel_users 中登记你的 open_id。"
)
# Group messages mention the bot as @_user_N placeholders; they are not card
# content and would only pollute the title line.
_MENTION_PLACEHOLDER = re.compile(r"@_user_\d+")
TEXT_LIMIT = 4000


class BridgeError(RuntimeError):
    """Configuration or transport failure the operator must fix."""


class Transport(Protocol):
    """One POST of a JSON body; returns (status, headers, parsed body).

    Production uses :class:`UrllibTransport`; tests inject stubs so the whole
    pipeline runs without leaving the machine.
    """

    def post_json(
        self,
        url: str,
        body: dict[str, Any],
        headers: dict[str, str] | None = None,
        timeout: float = 20,
    ) -> tuple[int, dict[str, str], Any]: ...


class UrllibTransport:
    """Real HTTP through the shared proxy policy (INWARD hub / OUTWARD feishu)."""

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
            raise BridgeError(f"POST {url}: {exc.reason}") from exc


# ---------- deployment configuration ----------


@dataclass
class BridgeConfig:
    """Resolved runtime settings; secrets arrive already dereferenced."""

    server_url: str
    channel_id: str
    channel_token: str
    listen_host: str = "127.0.0.1"
    listen_port: int = 9221
    verification_token: str | None = None
    app_id: str | None = None
    app_secret: str | None = None
    webhook_url: str | None = None
    registration_guide: str = DEFAULT_REGISTRATION_GUIDE
    feishu_api_base: str = FEISHU_API_BASE

    @property
    def reply_mode(self) -> str:
        """Credential-gated receipt channel: app API > group webhook > none."""
        if self.app_id and self.app_secret:
            return "app"
        if self.webhook_url:
            return "webhook"
        return "none"


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

    listen = raw.get("listen") or {}
    feishu = raw.get("feishu") or {}
    reply = raw.get("reply") or {}

    token = _secret(raw, "channel_token_env", "channel_token_file")
    if not token:
        raise BridgeError(
            "config needs channel_token_env or channel_token_file: "
            "the bridge cannot open cards without its channel credential"
        )
    server_url = str(raw.get("server_url") or "").strip()
    if not server_url:
        raise BridgeError("config needs server_url")
    return BridgeConfig(
        server_url=server_url.rstrip("/"),
        channel_id=str(raw.get("channel_id") or "feishu").strip() or "feishu",
        channel_token=token,
        listen_host=str(listen.get("host") or "127.0.0.1").strip(),
        listen_port=int(listen.get("port") or 9221),
        verification_token=_secret(
            feishu, "verification_token_env", "verification_token_file"
        ),
        app_id=str(feishu.get("app_id") or "").strip() or None,
        app_secret=_secret(feishu, "app_secret_env", "app_secret_file"),
        webhook_url=_secret(reply, "webhook_url_env", "webhook_url_file"),
        registration_guide=str(
            raw.get("registration_guide") or DEFAULT_REGISTRATION_GUIDE
        ).strip(),
        feishu_api_base=str(feishu.get("api_base") or FEISHU_API_BASE).rstrip("/"),
    )


# ---------- Feishu event normalization ----------


@dataclass
class InboundMessage:
    sender_id: str
    text: str
    message_id: str
    chat_id: str


@dataclass
class ParsedEvent:
    """One callback payload, reduced to what the pipeline branches on."""

    kind: str  # "challenge" | "message" | "ignored"
    challenge: str | None = None
    token: str | None = None
    message: InboundMessage | None = None
    reason: str | None = None


def _message_text(content_raw: str) -> str | None:
    try:
        content = json.loads(content_raw)
    except (TypeError, json.JSONDecodeError):
        return None
    text = content.get("text")
    if not isinstance(text, str):
        return None
    text = _MENTION_PLACEHOLDER.sub("", text).strip()
    return text[:TEXT_LIMIT] or None


def parse_event(payload: dict[str, Any]) -> ParsedEvent:
    """Normalize one Feishu event-callback payload (schema 2.0).

    Only ``im.message.receive_v1`` text messages from human senders become
    inbound cards; everything else — bot messages, rich types, other event
    kinds — is acknowledged and ignored.
    """
    if payload.get("type") == "url_verification":
        return ParsedEvent(
            kind="challenge",
            challenge=str(payload.get("challenge") or ""),
            token=str(payload.get("token") or "") or None,
        )
    header = payload.get("header") or {}
    token = str(header.get("token") or "") or None
    if header.get("event_type") != "im.message.receive_v1":
        return ParsedEvent(kind="ignored", token=token, reason="unhandled event type")
    event = payload.get("event") or {}
    sender = event.get("sender") or {}
    if sender.get("sender_type") not in (None, "user"):
        return ParsedEvent(kind="ignored", token=token, reason="bot sender")
    sender_id = (sender.get("sender_id") or {}).get("open_id")
    if not sender_id:
        return ParsedEvent(kind="ignored", token=token, reason="sender without open_id")
    message = event.get("message") or {}
    if message.get("message_type") != "text":
        return ParsedEvent(kind="ignored", token=token, reason="non-text message")
    text = _message_text(str(message.get("content") or ""))
    message_id = str(message.get("message_id") or "").strip()
    chat_id = str(message.get("chat_id") or "").strip()
    if not text or not message_id or not chat_id:
        return ParsedEvent(kind="ignored", token=token, reason="empty text or ids")
    return ParsedEvent(
        kind="message",
        token=token,
        message=InboundMessage(
            sender_id=str(sender_id), text=text, message_id=message_id, chat_id=chat_id
        ),
    )


# ---------- receipt delivery ----------


class Replier(Protocol):
    def send(self, chat_id: str, text: str) -> None: ...


class WebhookReplier:
    """Group custom-bot webhook: delivers into the one group it belongs to."""

    def __init__(self, url: str, transport: Transport) -> None:
        self.url = url
        self.transport = transport

    def send(self, chat_id: str, text: str) -> None:
        status, _headers, _body = self.transport.post_json(
            self.url, {"msg_type": "text", "content": {"text": text}}, timeout=10
        )
        if status >= 400:
            raise BridgeError(f"group webhook reply failed: HTTP {status}")


class AppReplier:
    """App message API: replies into the originating chat via tenant token."""

    def __init__(
        self, api_base: str, app_id: str, app_secret: str, transport: Transport
    ) -> None:
        self.api_base = api_base
        self.app_id = app_id
        self.app_secret = app_secret
        self.transport = transport
        self._token: str | None = None
        self._token_expires_at = 0.0

    def _tenant_token(self) -> str:
        if self._token and time.time() < self._token_expires_at:
            return self._token
        status, _headers, body = self.transport.post_json(
            f"{self.api_base}/open-apis/auth/v3/tenant_access_token/internal",
            {"app_id": self.app_id, "app_secret": self.app_secret},
            timeout=10,
        )
        token = body.get("tenant_access_token") if isinstance(body, dict) else None
        if status >= 400 or not token:
            raise BridgeError(f"tenant token request failed: HTTP {status} {body}")
        expire = int(body.get("expire") or 7200)
        self._token = str(token)
        self._token_expires_at = time.time() + max(60, expire - 300)
        return self._token

    def send(self, chat_id: str, text: str) -> None:
        token = self._tenant_token()
        status, _headers, body = self.transport.post_json(
            f"{self.api_base}/open-apis/im/v1/messages?receive_id_type=chat_id",
            {
                "receive_id": chat_id,
                "msg_type": "text",
                "content": json.dumps({"text": text}, ensure_ascii=False),
            },
            headers={"Authorization": f"Bearer {token}"},
            timeout=10,
        )
        code = body.get("code") if isinstance(body, dict) else None
        if status >= 400 or code not in (None, 0):
            raise BridgeError(f"app reply failed: HTTP {status} {body}")


class NullReplier:
    """Degraded delivery: no receipt channel configured, so log the text."""

    def send(self, chat_id: str, text: str) -> None:
        LOG.warning(
            "no reply channel configured; receipt for chat %s stays undelivered: %s",
            chat_id,
            text,
        )


def build_replier(config: BridgeConfig, outward: Transport) -> Replier:
    mode = config.reply_mode
    if mode == "app":
        return AppReplier(
            config.feishu_api_base,
            config.app_id or "",
            config.app_secret or "",
            outward,
        )
    if mode == "webhook":
        return WebhookReplier(config.webhook_url or "", outward)
    return NullReplier()


# ---------- the bridge pipeline ----------


@dataclass
class EventResult:
    status: str  # "challenge" | "opened" | "guidance" | "ignored" | "error"
    detail: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {"status": self.status, **self.detail}


class FeishuIntakeBridge:
    """Feishu event in, hub card out, receipt back to the sender."""

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
        self.replier = replier or build_replier(config, self.outward)

    def _check_token(self, event: ParsedEvent) -> None:
        expected = self.config.verification_token
        if expected is not None and event.token != expected:
            raise PermissionError("飞书事件回调 verification token 不匹配")

    def open_card(self, message: InboundMessage) -> tuple[int, dict[str, str], Any]:
        return self.intake.post_json(
            f"{self.config.server_url}/api/intake/{self.config.channel_id}/webhook",
            {
                "sender_id": message.sender_id,
                "text": message.text,
                "message_id": message.message_id,
                "chat_id": message.chat_id,
            },
            headers={"Authorization": f"Bearer {self.config.channel_token}"},
        )

    def handle_event(self, payload: dict[str, Any]) -> EventResult:
        event = parse_event(payload)
        self._check_token(event)
        if event.kind == "challenge":
            return EventResult("challenge", {"challenge": event.challenge or ""})
        if event.kind != "message" or event.message is None:
            return EventResult("ignored", {"reason": event.reason or ""})
        message = event.message
        status, headers, body = self.open_card(message)
        if status == 200 and isinstance(body, dict) and body.get("task_id"):
            task_id = str(body["task_id"])
            receipt = f"已开卡 {task_id},等待执行者接单;进展会在此回复。"
            self.replier.send(message.chat_id, receipt)
            LOG.info("opened %s for %s", task_id, message.sender_id)
            return EventResult("opened", {"task_id": task_id, "receipt": receipt})
        if status == 403 and headers.get("x-intake-error") == "channel-user-unmapped":
            self.replier.send(message.chat_id, self.config.registration_guide)
            LOG.info("unmapped sender %s; registration guide sent", message.sender_id)
            return EventResult("guidance", {"sender_id": message.sender_id})
        LOG.error("intake refused message %s: HTTP %s %s", message.message_id, status, body)
        return EventResult("error", {"http_status": status, "body": body})


# ---------- event-callback HTTP listener ----------


def make_handler(bridge: FeishuIntakeBridge) -> type[BaseHTTPRequestHandler]:
    class FeishuCallbackHandler(BaseHTTPRequestHandler):
        """Feishu retries on non-200, so only token mismatch gets a 4xx."""

        server_version = "FeishuIntakeBridge/0.1"

        def do_GET(self) -> None:  # noqa: N802 - stdlib handler contract
            if self.path.rstrip("/") == "/healthz":
                self._respond(200, {"ok": True})
            else:
                self._respond(404, {"detail": "not found"})

        def do_POST(self) -> None:  # noqa: N802 - stdlib handler contract
            try:
                length = int(self.headers.get("Content-Length") or 0)
            except ValueError:
                length = 0
            raw = self.rfile.read(max(0, min(length, 1024 * 1024)))
            try:
                payload = json.loads(raw.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError):
                self._respond(400, {"detail": "invalid JSON"})
                return
            try:
                result = bridge.handle_event(payload)
            except PermissionError as exc:
                self._respond(403, {"detail": str(exc)})
                return
            except BridgeError as exc:
                LOG.error("event handling failed: %s", exc)
                self._respond(200, {"status": "error", "detail": str(exc)})
                return
            body = result.as_dict()
            self._respond(200, body)

        def _respond(self, status: int, body: dict[str, Any]) -> None:
            data = json.dumps(body, ensure_ascii=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def log_message(self, fmt: str, *args: Any) -> None:
            LOG.info("callback %s", fmt % args)

    return FeishuCallbackHandler


def serve(bridge: FeishuIntakeBridge) -> ThreadingHTTPServer:
    config = bridge.config
    server = ThreadingHTTPServer(
        (config.listen_host, config.listen_port), make_handler(bridge)
    )
    LOG.info(
        "feishu intake bridge listening on http://%s:%s (reply mode: %s)",
        config.listen_host,
        config.listen_port,
        config.reply_mode,
    )
    return server


# ---------- CLI ----------


def run_simulation(bridge: FeishuIntakeBridge, sources: list[str]) -> int:
    """Inject event payloads from files ('-' = stdin); never leaves localhost."""
    failures = 0
    for source in sources:
        raw = sys.stdin.read() if source == "-" else Path(source).read_text(
            encoding="utf-8"
        )
        documents = json.loads(raw)
        if isinstance(documents, dict):
            documents = [documents]
        for payload in documents:
            result = bridge.handle_event(payload)
            print(json.dumps(result.as_dict(), ensure_ascii=False))
            if result.status == "error":
                failures += 1
    return 1 if failures else 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", required=True, help="deployment feishu-bridge.yaml")
    parser.add_argument(
        "--simulate",
        action="append",
        default=[],
        metavar="FILE",
        help="inject simulated event JSON from FILE ('-' for stdin) and exit",
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
    bridge = FeishuIntakeBridge(config)
    if args.check_config:
        summary = dataclasses.asdict(config)
        for secret_field in ("channel_token", "app_secret", "verification_token"):
            if summary.get(secret_field):
                summary[secret_field] = "***"
        if summary.get("webhook_url"):
            summary["webhook_url"] = "***"
        summary["reply_mode"] = config.reply_mode
        print(json.dumps(summary, ensure_ascii=False, indent=2))
        return 0
    if args.simulate:
        return run_simulation(bridge, args.simulate)
    if config.reply_mode == "none":
        LOG.warning(
            "no receipt channel configured (neither app credentials nor a group "
            "webhook): cards open normally but senders get no reply"
        )
    server = serve(bridge)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        LOG.info("stopping")
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
