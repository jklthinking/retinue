"""Reminder delivery scanner and channel plugins (M0).

Due slots live in ``reminder_deliveries`` (schema v18). This module scans for
pending rows whose ``scheduled_for`` has passed, delivers them through
configured channel plugins, and never re-sends a slot once it reaches a
terminal status (``delivered`` or ``abandoned``).

The scan function is shaped like ``dispatch_v2.fire_due_schedules`` (due query
plus an idempotent side effect) but lives here so that module stays untouched.
Callers on the existing reclaim / ready sweep cadence invoke
:func:`deliver_due_reminders`; there is no separate daemon in M0.
"""

from __future__ import annotations

import datetime as dt
import json
import logging
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Protocol

import yaml
from sqlalchemy import select
from sqlalchemy.orm import Session

from .db import ReminderDelivery, TodoEvent, TodoItem, utcnow
from .http_client import RequestClass, open_url
from .todos import REMINDER_CHANNEL_DEFAULT, _iso, append_todo_event
from .deps import Principal


logger = logging.getLogger(__name__)

STATUS_PENDING = "pending"
STATUS_DELIVERED = "delivered"
STATUS_ABANDONED = "abandoned"

EVENT_DELIVERED = "reminder_delivered"
EVENT_CHANNEL_OK = "reminder_channel_ok"
EVENT_FAILED = "reminder_delivery_failed"
EVENT_ABANDONED = "reminder_abandoned"

CONFIG_FILENAME = "reminders.yaml"
REMINDER_ACTOR = "reminders"

_bound_data_dir: Path | None = None


def bind_data_dir(data_dir: Path | str | None) -> None:
    """Remember the process data directory for reclaim/ready sweeps."""
    global _bound_data_dir
    _bound_data_dir = Path(data_dir) if data_dir else None


def bound_data_dir() -> Path | None:
    return _bound_data_dir


HttpPost = Callable[[str, bytes, float], tuple[int, str]]


@dataclass
class ChannelConfig:
    enabled: bool = False
    url: str = ""
    timeout_seconds: float = 5.0
    detail_level: str = "title"  # title | detail


@dataclass
class RemindersConfig:
    enabled: bool = False
    max_attempts: int = 3
    default_channels: list[str] = field(default_factory=lambda: ["in_app"])
    channels: dict[str, ChannelConfig] = field(default_factory=dict)


def _system_principal() -> Principal:
    return Principal(
        kind="agent",
        name=REMINDER_ACTOR,
        actor_id=REMINDER_ACTOR,
        role="agent",
    )


def _aware(value: dt.datetime | None) -> dt.datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=dt.timezone.utc)
    return value.astimezone(dt.timezone.utc)


def load_reminders_config(data_dir: Path | str | None) -> RemindersConfig:
    """Load ``<data_dir>/reminders.yaml``. Missing file => inert defaults."""
    cfg = RemindersConfig()
    if not data_dir:
        return cfg
    path = Path(data_dir) / CONFIG_FILENAME
    if not path.is_file():
        return cfg
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError) as exc:
        logger.warning("reminders config unreadable: %s", exc.__class__.__name__)
        return cfg
    if not isinstance(raw, dict):
        return cfg
    cfg.enabled = bool(raw.get("enabled", False))
    try:
        cfg.max_attempts = max(1, int(raw.get("max_attempts", 3)))
    except (TypeError, ValueError):
        cfg.max_attempts = 3
    defaults = raw.get("default_channels")
    if isinstance(defaults, list) and defaults:
        cfg.default_channels = [
            str(item).strip() for item in defaults if str(item).strip()
        ]
    channels_raw = raw.get("channels") or {}
    if isinstance(channels_raw, dict):
        for name, body in channels_raw.items():
            if not isinstance(body, dict):
                continue
            slot = ChannelConfig(
                enabled=bool(body.get("enabled", False)),
                url=str(body.get("url") or "").strip(),
                detail_level=str(body.get("detail_level") or "title").strip()
                or "title",
            )
            try:
                slot.timeout_seconds = float(body.get("timeout_seconds", 5))
            except (TypeError, ValueError):
                slot.timeout_seconds = 5.0
            cfg.channels[str(name)] = slot
    return cfg


def _default_http_post(url: str, body: bytes, timeout: float) -> tuple[int, str]:
    request = urllib.request.Request(
        url,
        method="POST",
        data=body,
        headers={"Content-Type": "application/json"},
    )
    try:
        with open_url(
            request, timeout=timeout, request_class=RequestClass.OUTWARD
        ) as response:
            status = int(getattr(response, "status", 0) or response.getcode())
            return status, ""
    except urllib.error.HTTPError as exc:
        return int(exc.code), exc.__class__.__name__
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        # Never log the URL (may embed credentials in operator configs).
        return 0, exc.__class__.__name__


class DeliveryChannel(Protocol):
    name: str

    def already_delivered(self, db: Session, row: ReminderDelivery) -> bool: ...

    def deliver(
        self,
        db: Session,
        row: ReminderDelivery,
        item: TodoItem,
        *,
        config: ChannelConfig,
        http_post: HttpPost,
    ) -> None: ...


def _channel_ok_exists(db: Session, row: ReminderDelivery, channel: str) -> bool:
    events = db.execute(
        select(TodoEvent).where(
            TodoEvent.todo_item_id == row.todo_item_id,
            TodoEvent.event_type == EVENT_CHANNEL_OK,
        )
    ).scalars()
    for event in events:
        try:
            payload = json.loads(event.payload_json or "{}")
        except json.JSONDecodeError:
            continue
        if (
            payload.get("delivery_key") == row.delivery_key
            and payload.get("channel") == channel
        ):
            return True
    return False


def _failure_count(db: Session, row: ReminderDelivery) -> int:
    events = db.execute(
        select(TodoEvent).where(
            TodoEvent.todo_item_id == row.todo_item_id,
            TodoEvent.event_type == EVENT_FAILED,
        )
    ).scalars()
    count = 0
    for event in events:
        try:
            payload = json.loads(event.payload_json or "{}")
        except json.JSONDecodeError:
            continue
        if payload.get("delivery_key") == row.delivery_key:
            count += 1
    return count


def _record_channel_ok(
    db: Session, row: ReminderDelivery, item: TodoItem, channel: str
) -> None:
    append_todo_event(
        db,
        principal=_system_principal(),
        event_type=EVENT_CHANNEL_OK,
        did=f"reminder delivered via {channel}",
        item=item,
        payload={
            "delivery_key": row.delivery_key,
            "channel": channel,
            "scheduled_for": _iso(row.scheduled_for),
        },
    )


def _record_failure(
    db: Session,
    row: ReminderDelivery,
    item: TodoItem,
    *,
    channel: str,
    error_class: str,
) -> None:
    append_todo_event(
        db,
        principal=_system_principal(),
        event_type=EVENT_FAILED,
        did=f"reminder channel {channel} failed",
        item=item,
        payload={
            "delivery_key": row.delivery_key,
            "channel": channel,
            "error_class": error_class,
        },
    )


class InAppChannel:
    name = "in_app"

    def already_delivered(self, db: Session, row: ReminderDelivery) -> bool:
        return _channel_ok_exists(db, row, self.name)

    def deliver(
        self,
        db: Session,
        row: ReminderDelivery,
        item: TodoItem,
        *,
        config: ChannelConfig,
        http_post: HttpPost,
    ) -> None:
        del config, http_post
        if self.already_delivered(db, row):
            return
        # Owner-visible audit on the private todo event chain (no shared card).
        append_todo_event(
            db,
            principal=_system_principal(),
            event_type=EVENT_DELIVERED,
            did=f"reminder due: {item.title[:200]}",
            item=item,
            payload={
                "delivery_key": row.delivery_key,
                "channel": self.name,
                "title": item.title,
                "scheduled_for": _iso(row.scheduled_for),
            },
        )
        _record_channel_ok(db, row, item, self.name)


class WebhookChannel:
    name = "webhook"

    def already_delivered(self, db: Session, row: ReminderDelivery) -> bool:
        return _channel_ok_exists(db, row, self.name)

    def deliver(
        self,
        db: Session,
        row: ReminderDelivery,
        item: TodoItem,
        *,
        config: ChannelConfig,
        http_post: HttpPost,
    ) -> None:
        if self.already_delivered(db, row):
            return
        if not config.url:
            raise RuntimeError("webhook_url_missing")
        payload: dict[str, Any] = {
            "title": item.title,
            "scheduled_for": _iso(row.scheduled_for),
        }
        if config.detail_level == "detail":
            payload.update(
                {
                    "todo_item_id": item.id,
                    "delivery_key": row.delivery_key,
                    "notes": item.notes or "",
                    "due_at": _iso(item.due_at),
                }
            )
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        status, err = http_post(config.url, body, float(config.timeout_seconds))
        if status < 200 or status >= 300:
            raise RuntimeError(err or f"http_{status}")
        _record_channel_ok(db, row, item, self.name)


CHANNEL_PLUGINS: dict[str, DeliveryChannel] = {
    InAppChannel.name: InAppChannel(),
    WebhookChannel.name: WebhookChannel(),
}


def _resolve_channel_names(row: ReminderDelivery, config: RemindersConfig) -> list[str]:
    slot = (row.channel or REMINDER_CHANNEL_DEFAULT).strip() or REMINDER_CHANNEL_DEFAULT
    if slot in CHANNEL_PLUGINS:
        return [slot]
    # Legacy / default registration slot ("pending") fans out to configured defaults.
    names = list(config.default_channels)
    return [name for name in names if name in CHANNEL_PLUGINS]


def list_globally_due_reminders(
    db: Session, *, as_of: dt.datetime | None = None
) -> list[ReminderDelivery]:
    """All pending reminder slots due at or before ``as_of`` (any owner)."""
    horizon = _aware(as_of) or utcnow()
    return list(
        db.execute(
            select(ReminderDelivery)
            .where(
                ReminderDelivery.status == STATUS_PENDING,
                ReminderDelivery.scheduled_for <= horizon,
            )
            .order_by(ReminderDelivery.scheduled_for, ReminderDelivery.id)
        ).scalars()
    )


def deliver_due_reminders(
    db: Session,
    *,
    now: dt.datetime | None = None,
    data_dir: Path | str | None = None,
    config: RemindersConfig | None = None,
    http_post: HttpPost | None = None,
) -> dict[str, Any]:
    """Deliver due pending reminder slots. Safe to call repeatedly.

    Idempotency rests on ``reminder_deliveries.delivery_key`` (unique) plus the
    terminal ``status`` transition. Channel plugins also record per-channel
    success events so a retry never re-POSTs a webhook that already succeeded.
    """
    cfg = config if config is not None else load_reminders_config(data_dir)
    clock = _aware(now) or utcnow()
    poster = http_post or _default_http_post
    results: list[dict[str, Any]] = []
    if not cfg.enabled:
        return {"delivered": results, "count": 0, "enabled": False}

    due = list_globally_due_reminders(db, as_of=clock)
    for row in due:
        item = db.get(TodoItem, row.todo_item_id)
        if item is None:
            row.status = STATUS_ABANDONED
            row.delivered_at = clock
            results.append(
                {
                    "delivery_key": row.delivery_key,
                    "action": "abandoned",
                    "reason": "missing_todo",
                }
            )
            continue

        channel_names = _resolve_channel_names(row, cfg)
        enabled_targets: list[tuple[str, DeliveryChannel, ChannelConfig]] = []
        for name in channel_names:
            plugin = CHANNEL_PLUGINS.get(name)
            if plugin is None:
                continue
            ch_cfg = cfg.channels.get(name) or ChannelConfig(enabled=(name == "in_app"))
            if name == "in_app" and name not in cfg.channels:
                ch_cfg = ChannelConfig(enabled=True)
            if not ch_cfg.enabled:
                continue
            enabled_targets.append((name, plugin, ch_cfg))

        if not enabled_targets:
            # Nothing configured to accept the slot; leave pending.
            results.append(
                {
                    "delivery_key": row.delivery_key,
                    "action": "skipped",
                    "reason": "no_enabled_channels",
                }
            )
            continue

        pending_channels = [
            (name, plugin, ch_cfg)
            for name, plugin, ch_cfg in enabled_targets
            if not plugin.already_delivered(db, row)
        ]
        if not pending_channels:
            row.status = STATUS_DELIVERED
            row.delivered_at = row.delivered_at or clock
            results.append(
                {
                    "delivery_key": row.delivery_key,
                    "action": "delivered",
                    "channels": [name for name, _, _ in enabled_targets],
                }
            )
            continue

        failures: list[str] = []
        for name, plugin, ch_cfg in pending_channels:
            try:
                plugin.deliver(
                    db, row, item, config=ch_cfg, http_post=poster
                )
            except Exception as exc:  # noqa: BLE001 — channel boundary
                err_name = exc.__class__.__name__
                if str(exc):
                    # Keep the short stable token only (no URL / body).
                    token = str(exc).split()[0][:64]
                    if token.isidentifier() or token.startswith("http_"):
                        err_name = token
                _record_failure(
                    db, row, item, channel=name, error_class=err_name
                )
                failures.append(name)

        if failures:
            attempts = _failure_count(db, row)
            if attempts >= cfg.max_attempts:
                row.status = STATUS_ABANDONED
                row.delivered_at = clock
                append_todo_event(
                    db,
                    principal=_system_principal(),
                    event_type=EVENT_ABANDONED,
                    did="reminder delivery abandoned after retry limit",
                    item=item,
                    payload={
                        "delivery_key": row.delivery_key,
                        "attempts": attempts,
                        "failed_channels": failures,
                    },
                )
                results.append(
                    {
                        "delivery_key": row.delivery_key,
                        "action": "abandoned",
                        "attempts": attempts,
                        "failed_channels": failures,
                    }
                )
            else:
                results.append(
                    {
                        "delivery_key": row.delivery_key,
                        "action": "retry",
                        "attempts": attempts,
                        "failed_channels": failures,
                    }
                )
            continue

        row.status = STATUS_DELIVERED
        row.delivered_at = clock
        results.append(
            {
                "delivery_key": row.delivery_key,
                "action": "delivered",
                "channels": [name for name, _, _ in enabled_targets],
            }
        )

    db.flush()
    return {
        "delivered": results,
        "count": sum(1 for row in results if row.get("action") == "delivered"),
        "enabled": True,
    }
