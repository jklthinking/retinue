"""Notifier plugin layer (M0): dual channels, idempotent deliveries, refresh hook.

Sends go through a small plugin interface. Three channels ship in M0:

- ``group_webhook`` — Feishu/Lark custom-bot webhook (legacy
  ``RETINUE_FEISHU_WEBHOOK`` behaviour).
- ``tenant_app`` — Feishu tenant-app IM messages (token + im/v1/messages),
  all HTTP via an injectable client; missing credentials degrade to the log
  channel without raising.
- ``log`` — structured log fallback.

Deployment config lives in ``<data-dir>/notify.yaml`` (template:
``docs/examples/notify.yaml``). Secrets are env-var indirection only.
"""

from __future__ import annotations

import json
import logging
import os
import threading
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Mapping, Protocol

import yaml
from sqlalchemy import select
from sqlalchemy.orm import Session

from .db import NotificationDelivery, utcnow
from .http_client import RequestClass, open_url


logger = logging.getLogger(__name__)

CONFIG_FILENAME = "notify.yaml"

STATUS_PENDING = "pending"
STATUS_DELIVERED = "delivered"
STATUS_FAILED = "failed"
STATUS_ABANDONED = "abandoned"

CHANNEL_GROUP_WEBHOOK = "group_webhook"
CHANNEL_TENANT_APP = "tenant_app"
CHANNEL_LOG = "log"

DEFAULT_FEISHU_API_BASE = "https://open.feishu.cn"
DEFAULT_WEBHOOK_URL_ENV = "RETINUE_FEISHU_WEBHOOK"
DEFAULT_APP_SECRET_ENV = "RETINUE_FEISHU_APP_SECRET"

# method, url, body, headers, timeout -> (status_code, response_body)
HttpClient = Callable[
    [str, str, bytes | None, dict[str, str], float], tuple[int, bytes]
]

_bound_data_dir: Path | None = None


def bind_data_dir(data_dir: Path | str | None) -> None:
    """Remember the process data directory for hang-point callers."""
    global _bound_data_dir
    _bound_data_dir = Path(data_dir) if data_dir else None


def bound_data_dir() -> Path | None:
    return _bound_data_dir


@dataclass
class ChannelConfig:
    enabled: bool = True
    webhook_url_env: str = DEFAULT_WEBHOOK_URL_ENV
    app_id: str = ""
    app_secret_env: str = DEFAULT_APP_SECRET_ENV
    api_base: str = DEFAULT_FEISHU_API_BASE
    receive_id_type: str = "chat_id"
    timeout_seconds: float = 10.0


@dataclass
class NotifyConfig:
    """Loaded notify.yaml. Empty default_channel ⇒ legacy auto-detect."""

    default_channel: str = ""
    channels: dict[str, ChannelConfig] = field(default_factory=dict)


@dataclass
class SendResult:
    ok: bool
    channel: str
    status: str  # delivered | failed | abandoned | skipped | degraded
    message_ref: str | None = None
    detail: str = ""


@dataclass
class RefreshResult:
    ok: bool
    channel: str
    status: str  # refreshed | stubbed | skipped | failed
    message_ref: str | None = None
    detail: str = ""


def _default_http_client(
    method: str,
    url: str,
    body: bytes | None,
    headers: dict[str, str],
    timeout: float,
) -> tuple[int, bytes]:
    request = urllib.request.Request(
        url,
        method=method.upper(),
        data=body,
        headers=headers,
    )
    try:
        with open_url(
            request, timeout=timeout, request_class=RequestClass.OUTWARD
        ) as response:
            status = int(getattr(response, "status", 0) or response.getcode())
            return status, response.read()
    except urllib.error.HTTPError as exc:
        return int(exc.code), exc.read() if exc.fp is not None else b""
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        # Never log the URL (may embed operator-configured credentials).
        return 0, exc.__class__.__name__.encode("utf-8")


def load_notify_config(data_dir: Path | str | None) -> NotifyConfig:
    """Load ``<data_dir>/notify.yaml``. Missing file ⇒ empty legacy defaults."""
    cfg = NotifyConfig()
    if not data_dir:
        return cfg
    path = Path(data_dir) / CONFIG_FILENAME
    if not path.is_file():
        return cfg
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError) as exc:
        logger.warning("notify config unreadable: %s", exc.__class__.__name__)
        return cfg
    if not isinstance(raw, dict):
        return cfg
    default = str(raw.get("default_channel") or "").strip()
    if default:
        cfg.default_channel = default
    channels_raw = raw.get("channels") or {}
    if isinstance(channels_raw, dict):
        for name, body in channels_raw.items():
            if not isinstance(body, dict):
                continue
            slot = ChannelConfig(
                enabled=bool(body.get("enabled", True)),
                webhook_url_env=str(
                    body.get("webhook_url_env") or DEFAULT_WEBHOOK_URL_ENV
                ).strip()
                or DEFAULT_WEBHOOK_URL_ENV,
                app_id=str(body.get("app_id") or "").strip(),
                app_secret_env=str(
                    body.get("app_secret_env") or DEFAULT_APP_SECRET_ENV
                ).strip()
                or DEFAULT_APP_SECRET_ENV,
                api_base=str(body.get("api_base") or DEFAULT_FEISHU_API_BASE)
                .strip()
                .rstrip("/")
                or DEFAULT_FEISHU_API_BASE,
                receive_id_type=str(body.get("receive_id_type") or "chat_id").strip()
                or "chat_id",
            )
            try:
                slot.timeout_seconds = float(body.get("timeout_seconds", 10))
            except (TypeError, ValueError):
                slot.timeout_seconds = 10.0
            cfg.channels[str(name)] = slot
    return cfg


def _channel_config(cfg: NotifyConfig, name: str) -> ChannelConfig:
    return cfg.channels.get(name) or ChannelConfig()


def resolve_channel_name(
    cfg: NotifyConfig, *, explicit: str | None = None
) -> str:
    """Pick the active channel: explicit > config default > legacy auto."""
    if explicit and explicit.strip():
        return explicit.strip()
    if cfg.default_channel:
        return cfg.default_channel
    webhook_env = DEFAULT_WEBHOOK_URL_ENV
    slot = cfg.channels.get(CHANNEL_GROUP_WEBHOOK)
    if slot is not None:
        webhook_env = slot.webhook_url_env or DEFAULT_WEBHOOK_URL_ENV
    if os.environ.get(webhook_env, "").strip():
        return CHANNEL_GROUP_WEBHOOK
    return CHANNEL_LOG


def _env_secret(env_name: str) -> str:
    if not env_name:
        return ""
    return os.environ.get(env_name, "").strip()


class NotifyChannel(Protocol):
    name: str

    def send(
        self,
        target: str,
        payload: Mapping[str, Any],
        *,
        config: ChannelConfig,
        http_client: HttpClient,
    ) -> SendResult: ...

    def refresh(
        self,
        message_ref: str,
        approval_result: Mapping[str, Any],
        *,
        config: ChannelConfig,
        http_client: HttpClient,
    ) -> RefreshResult: ...


def _degrade_to_log(
    from_channel: str,
    target: str,
    payload: Mapping[str, Any],
    config: ChannelConfig,
    http_client: HttpClient,
) -> SendResult:
    """Run the log channel and mark the outcome as a credential degrade."""
    logged = LogChannel().send(
        target, payload, config=config, http_client=http_client
    )
    return SendResult(
        ok=logged.ok,
        channel=from_channel,
        status="degraded",
        message_ref=logged.message_ref,
        detail=f"degraded_to_log:{logged.detail}",
    )


class GroupWebhookChannel:
    name = CHANNEL_GROUP_WEBHOOK

    def send(
        self,
        target: str,
        payload: Mapping[str, Any],
        *,
        config: ChannelConfig,
        http_client: HttpClient,
    ) -> SendResult:
        url = (target or "").strip() or _env_secret(config.webhook_url_env)
        if not url:
            return SendResult(
                ok=False,
                channel=self.name,
                status="skipped",
                detail="webhook_url_missing",
            )
        body = json.dumps(dict(payload), ensure_ascii=False).encode("utf-8")
        status, raw = http_client(
            "POST",
            url,
            body,
            {"Content-Type": "application/json"},
            config.timeout_seconds,
        )
        if status == 0 or status >= 400:
            detail = raw.decode("utf-8", errors="replace")[:120] if raw else "http_error"
            return SendResult(
                ok=False,
                channel=self.name,
                status="failed",
                detail=detail or f"HTTP {status}",
            )
        return SendResult(
            ok=True,
            channel=self.name,
            status=STATUS_DELIVERED,
            message_ref=None,
            detail=f"HTTP {status}",
        )

    def refresh(
        self,
        message_ref: str,
        approval_result: Mapping[str, Any],
        *,
        config: ChannelConfig,
        http_client: HttpClient,
    ) -> RefreshResult:
        del approval_result, config, http_client
        return RefreshResult(
            ok=True,
            channel=self.name,
            status="stubbed",
            message_ref=message_ref,
            detail="group_webhook_refresh_unsupported",
        )


class TenantAppChannel:
    name = CHANNEL_TENANT_APP

    def send(
        self,
        target: str,
        payload: Mapping[str, Any],
        *,
        config: ChannelConfig,
        http_client: HttpClient,
    ) -> SendResult:
        app_id = (config.app_id or "").strip()
        app_secret = _env_secret(config.app_secret_env)
        if not app_id or not app_secret:
            logger.warning(
                "tenant_app credentials missing (app_id or %s); degrading to log",
                config.app_secret_env or DEFAULT_APP_SECRET_ENV,
            )
            return _degrade_to_log(self.name, target, payload, config, http_client)

        receive_id = (target or "").strip()
        if not receive_id:
            logger.warning("tenant_app send missing target receive_id; degrading to log")
            return _degrade_to_log(self.name, target, payload, config, http_client)

        token = self._tenant_token(config, http_client, app_id, app_secret)
        if not token:
            logger.warning("tenant_app token fetch failed; degrading to log")
            return _degrade_to_log(self.name, target, payload, config, http_client)

        msg_type = str(payload.get("msg_type") or "text")
        content = payload.get("content")
        if isinstance(content, (dict, list)):
            content_str = json.dumps(content, ensure_ascii=False)
        elif content is None and "text" in payload:
            content_str = json.dumps(
                {"text": str(payload["text"])}, ensure_ascii=False
            )
            msg_type = "text"
        else:
            content_str = str(content or "")

        api_body = json.dumps(
            {
                "receive_id": receive_id,
                "msg_type": msg_type,
                "content": content_str,
            },
            ensure_ascii=False,
        ).encode("utf-8")
        url = (
            f"{config.api_base}/open-apis/im/v1/messages"
            f"?receive_id_type={config.receive_id_type}"
        )
        status, raw = http_client(
            "POST",
            url,
            api_body,
            {
                "Content-Type": "application/json",
                "Authorization": f"Bearer {token}",
            },
            config.timeout_seconds,
        )
        message_ref = None
        code: Any = None
        try:
            parsed = json.loads(raw.decode("utf-8") or "null")
        except (UnicodeDecodeError, json.JSONDecodeError):
            parsed = None
        if isinstance(parsed, dict):
            data = parsed.get("data")
            if isinstance(data, dict):
                mid = data.get("message_id")
                if mid:
                    message_ref = str(mid)
            code = parsed.get("code")
        if status == 0 or status >= 400 or (code not in (None, 0)):
            return SendResult(
                ok=False,
                channel=self.name,
                status=STATUS_FAILED,
                message_ref=message_ref,
                detail=f"HTTP {status} code={code}",
            )
        return SendResult(
            ok=True,
            channel=self.name,
            status=STATUS_DELIVERED,
            message_ref=message_ref,
            detail=f"HTTP {status}",
        )

    def _tenant_token(
        self,
        config: ChannelConfig,
        http_client: HttpClient,
        app_id: str,
        app_secret: str,
    ) -> str | None:
        url = f"{config.api_base}/open-apis/auth/v3/tenant_access_token/internal"
        body = json.dumps(
            {"app_id": app_id, "app_secret": app_secret}, ensure_ascii=False
        ).encode("utf-8")
        status, raw = http_client(
            "POST",
            url,
            body,
            {"Content-Type": "application/json"},
            config.timeout_seconds,
        )
        try:
            parsed = json.loads(raw.decode("utf-8") or "null")
        except (UnicodeDecodeError, json.JSONDecodeError):
            return None
        if status == 0 or status >= 400 or not isinstance(parsed, dict):
            return None
        token = parsed.get("tenant_access_token")
        return str(token) if token else None

    def refresh(
        self,
        message_ref: str,
        approval_result: Mapping[str, Any],
        *,
        config: ChannelConfig,
        http_client: HttpClient,
    ) -> RefreshResult:
        del config, http_client
        # M0 stub: real Feishu card patch lands after credential wiring.
        decision = approval_result.get("decision")
        logger.info(
            "tenant_app refresh stub message_ref=%s decision=%s",
            message_ref,
            decision,
        )
        return RefreshResult(
            ok=True,
            channel=self.name,
            status="stubbed",
            message_ref=message_ref,
            detail="tenant_app_refresh_stub",
        )


class LogChannel:
    name = CHANNEL_LOG

    def send(
        self,
        target: str,
        payload: Mapping[str, Any],
        *,
        config: ChannelConfig,
        http_client: HttpClient,
    ) -> SendResult:
        del config, http_client
        logger.info(
            "notify log channel target=%s payload_keys=%s",
            target or "-",
            sorted(str(k) for k in payload.keys()),
        )
        return SendResult(
            ok=True,
            channel=self.name,
            status=STATUS_DELIVERED,
            message_ref=None,
            detail="logged",
        )

    def refresh(
        self,
        message_ref: str,
        approval_result: Mapping[str, Any],
        *,
        config: ChannelConfig,
        http_client: HttpClient,
    ) -> RefreshResult:
        del config, http_client
        logger.info(
            "notify log refresh message_ref=%s decision=%s",
            message_ref,
            approval_result.get("decision"),
        )
        return RefreshResult(
            ok=True,
            channel=self.name,
            status="stubbed",
            message_ref=message_ref,
            detail="log_refresh",
        )


CHANNEL_PLUGINS: dict[str, NotifyChannel] = {
    GroupWebhookChannel.name: GroupWebhookChannel(),
    TenantAppChannel.name: TenantAppChannel(),
    LogChannel.name: LogChannel(),
}


def get_channel(name: str) -> NotifyChannel:
    plugin = CHANNEL_PLUGINS.get(name)
    if plugin is None:
        logger.warning("unknown notify channel %s; using log", name)
        return CHANNEL_PLUGINS[CHANNEL_LOG]
    return plugin


def send(
    target: str,
    payload: Mapping[str, Any],
    *,
    channel: str | None = None,
    data_dir: Path | str | None = None,
    config: NotifyConfig | None = None,
    http_client: HttpClient | None = None,
) -> SendResult:
    """Plugin entry: send ``payload`` to ``target`` via the resolved channel."""
    cfg = config if config is not None else load_notify_config(
        data_dir if data_dir is not None else bound_data_dir()
    )
    name = resolve_channel_name(cfg, explicit=channel)
    plugin = get_channel(name)
    slot = _channel_config(cfg, name)
    if not slot.enabled and name != CHANNEL_LOG:
        logger.info("notify channel %s disabled; using log", name)
        plugin = CHANNEL_PLUGINS[CHANNEL_LOG]
        name = CHANNEL_LOG
        slot = _channel_config(cfg, name)
    client = http_client or _default_http_client
    try:
        return plugin.send(target, payload, config=slot, http_client=client)
    except Exception as exc:  # noqa: BLE001 — notifications never break callers
        logger.warning(
            "notify channel %s raised %s; swallowed",
            name,
            exc.__class__.__name__,
        )
        return SendResult(
            ok=False,
            channel=name,
            status=STATUS_FAILED,
            detail=exc.__class__.__name__,
        )


def deliver(
    db: Session,
    *,
    dedupe_key: str,
    target: str,
    payload: Mapping[str, Any],
    channel: str | None = None,
    data_dir: Path | str | None = None,
    config: NotifyConfig | None = None,
    http_client: HttpClient | None = None,
) -> dict[str, Any]:
    """Idempotent send: same ``dedupe_key`` never re-delivers a terminal row."""
    key = (dedupe_key or "").strip()
    if not key:
        raise ValueError("dedupe_key is required")

    existing = db.execute(
        select(NotificationDelivery).where(NotificationDelivery.dedupe_key == key)
    ).scalar_one_or_none()
    if existing is not None and existing.status in {
        STATUS_DELIVERED,
        STATUS_ABANDONED,
    }:
        return {
            "action": "skipped",
            "reason": "already_terminal",
            "dedupe_key": key,
            "status": existing.status,
            "channel": existing.channel,
            "message_ref": existing.message_ref,
            "attempts": existing.attempts,
        }

    cfg = config if config is not None else load_notify_config(
        data_dir if data_dir is not None else bound_data_dir()
    )
    name = resolve_channel_name(cfg, explicit=channel)
    now = utcnow()
    row = existing
    if row is None:
        row = NotificationDelivery(
            dedupe_key=key,
            channel=name,
            target=target or "",
            status=STATUS_PENDING,
            attempts=0,
            message_ref=None,
            created_at=now,
            updated_at=now,
        )
        db.add(row)
        db.flush()

    result = send(
        target,
        payload,
        channel=name,
        data_dir=data_dir,
        config=cfg,
        http_client=http_client,
    )
    row.attempts = int(row.attempts or 0) + 1
    row.channel = result.channel
    row.target = target or row.target
    row.updated_at = utcnow()
    if result.message_ref:
        row.message_ref = result.message_ref
    if result.status == "skipped":
        row.status = STATUS_ABANDONED
        action = "abandoned"
    elif result.status == "degraded":
        row.status = STATUS_DELIVERED
        action = "degraded"
    elif result.ok:
        row.status = STATUS_DELIVERED
        action = "delivered"
    else:
        row.status = STATUS_FAILED
        action = "failed"
    db.flush()
    return {
        "action": action,
        "dedupe_key": key,
        "status": row.status,
        "channel": row.channel,
        "message_ref": row.message_ref,
        "attempts": row.attempts,
        "detail": result.detail,
    }


def refresh_after_approval(
    db: Session,
    approval_result: Mapping[str, Any],
    *,
    data_dir: Path | str | None = None,
    config: NotifyConfig | None = None,
    http_client: HttpClient | None = None,
) -> dict[str, Any]:
    """Approval-settled refresh hook: look up ``message_ref`` and call channel.refresh.

    ``approval_result`` may carry ``message_ref`` and/or ``dedupe_key`` to locate
    the prior delivery. Tenant-app refresh is a recorded stub in M0.
    """
    message_ref = str(approval_result.get("message_ref") or "").strip()
    dedupe_key = str(approval_result.get("dedupe_key") or "").strip()
    row: NotificationDelivery | None = None
    if dedupe_key:
        row = db.execute(
            select(NotificationDelivery).where(
                NotificationDelivery.dedupe_key == dedupe_key
            )
        ).scalar_one_or_none()
    if row is None and message_ref:
        row = db.execute(
            select(NotificationDelivery).where(
                NotificationDelivery.message_ref == message_ref
            )
        ).scalar_one_or_none()
    if row is None:
        return {
            "action": "skipped",
            "reason": "delivery_not_found",
            "message_ref": message_ref or None,
            "dedupe_key": dedupe_key or None,
        }
    ref = (row.message_ref or message_ref or "").strip()
    if not ref:
        return {
            "action": "skipped",
            "reason": "message_ref_missing",
            "dedupe_key": row.dedupe_key,
            "channel": row.channel,
        }

    cfg = config if config is not None else load_notify_config(
        data_dir if data_dir is not None else bound_data_dir()
    )
    plugin = get_channel(row.channel)
    slot = _channel_config(cfg, row.channel)
    client = http_client or _default_http_client
    try:
        outcome = plugin.refresh(
            ref,
            approval_result,
            config=slot,
            http_client=client,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "notify refresh raised %s; swallowed", exc.__class__.__name__
        )
        return {
            "action": "failed",
            "channel": row.channel,
            "message_ref": ref,
            "dedupe_key": row.dedupe_key,
            "detail": exc.__class__.__name__,
        }

    row.updated_at = utcnow()
    db.flush()
    return {
        "action": outcome.status,
        "ok": outcome.ok,
        "channel": outcome.channel,
        "message_ref": outcome.message_ref or ref,
        "dedupe_key": row.dedupe_key,
        "detail": outcome.detail,
    }


def legacy_group_webhook_payload_text(text: str) -> dict[str, Any]:
    """Shape used by the historical custom-bot text notifier."""
    return {"msg_type": "text", "content": {"text": text}}


def fire_and_forget_group_webhook(
    payload: Mapping[str, Any],
    *,
    data_dir: Path | str | None = None,
) -> None:
    """Background group-webhook post; mirrors pre-M0 threading + silence.

    Always targets ``group_webhook`` so interactive cards keep the custom-bot
    path even when ``default_channel`` is ``tenant_app``.
    """
    cfg = load_notify_config(
        data_dir if data_dir is not None else bound_data_dir()
    )
    slot = _channel_config(cfg, CHANNEL_GROUP_WEBHOOK)
    webhook_env = slot.webhook_url_env or DEFAULT_WEBHOOK_URL_ENV
    if not _env_secret(webhook_env):
        return

    def _send() -> None:
        try:
            send(
                "",
                payload,
                channel=CHANNEL_GROUP_WEBHOOK,
                data_dir=data_dir,
                config=cfg,
            )
        except Exception:  # noqa: BLE001
            pass

    threading.Thread(target=_send, daemon=True).start()
