"""Reminder delivery scanner M0: due clock, webhook, idempotency, retry limit."""

from __future__ import annotations

import datetime as dt
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import yaml
from sqlalchemy import select

from server.db import (
    ReminderDelivery,
    TodoEvent,
    TodoItem,
    User,
    make_session_factory,
)
from server.reminders import (
    EVENT_ABANDONED,
    EVENT_CHANNEL_OK,
    EVENT_DELIVERED,
    EVENT_FAILED,
    ChannelConfig,
    RemindersConfig,
    deliver_due_reminders,
    load_reminders_config,
)
from server.security import hash_password


NOW = dt.datetime(2026, 8, 13, 12, 0, tzinfo=dt.timezone.utc)
PAST = NOW - dt.timedelta(minutes=10)
FUTURE = NOW + dt.timedelta(hours=2)


def _seed(tmp_path: Path):
    factory = make_session_factory(tmp_path / "reminders.db")
    with factory() as db:
        user = User(
            username="owner",
            password_hash=hash_password("owner-pass-1"),
            role="member",
        )
        db.add(user)
        db.flush()
        item = TodoItem(
            id="todo-20260813-001",
            owner_user_id=user.id,
            title="Water the plants",
            notes="balcony shelf",
            status="open",
            remind_at=PAST,
        )
        db.add(item)
        db.flush()
        key = f"{item.id}:{PAST.isoformat().replace('+00:00', 'Z')}:pending"
        row = ReminderDelivery(
            todo_item_id=item.id,
            owner_user_id=user.id,
            scheduled_for=PAST,
            channel="pending",
            delivery_key=key,
            status="pending",
        )
        db.add(row)
        db.commit()
        return factory, user.id, item.id, key


def _write_config(data_dir: Path, **overrides) -> None:
    raw = {
        "enabled": True,
        "max_attempts": 3,
        "default_channels": ["in_app", "webhook"],
        "channels": {
            "in_app": {"enabled": True},
            "webhook": {
                "enabled": True,
                "url": "",
                "timeout_seconds": 2,
                "detail_level": "title",
            },
        },
    }
    for key, value in overrides.items():
        if key == "webhook":
            raw["channels"]["webhook"].update(value)
        elif key == "default_channels":
            raw["default_channels"] = value
        else:
            raw[key] = value
    data_dir.mkdir(parents=True, exist_ok=True)
    (data_dir / "reminders.yaml").write_text(
        yaml.safe_dump(raw, sort_keys=False), encoding="utf-8"
    )


class _WebhookHandler(BaseHTTPRequestHandler):
    server: "WebhookServer"

    def do_POST(self):  # noqa: N802
        length = int(self.headers.get("Content-Length", "0"))
        body = self.rfile.read(length)
        self.server.posts.append(
            {"path": self.path, "body": body, "headers": dict(self.headers)}
        )
        if self.server.fail_times > 0:
            self.server.fail_times -= 1
            self.send_response(500)
            self.end_headers()
            self.wfile.write(b"nope")
            return
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"ok")

    def log_message(self, format, *args):  # noqa: A003
        return


class WebhookServer(ThreadingHTTPServer):
    def __init__(self, address, fail_times: int = 0):
        super().__init__(address, _WebhookHandler)
        self.posts: list[dict] = []
        self.fail_times = fail_times


def _start_server(fail_times: int = 0) -> WebhookServer:
    server = WebhookServer(("127.0.0.1", 0), fail_times=fail_times)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


def test_load_config_missing_file_is_inert(tmp_path):
    cfg = load_reminders_config(tmp_path)
    assert cfg.enabled is False
    factory = make_session_factory(tmp_path / "empty.db")
    with factory() as db:
        result = deliver_due_reminders(db, data_dir=tmp_path, now=NOW)
        assert result["enabled"] is False
        assert result["count"] == 0


def test_due_clock_skips_future_slots(tmp_path):
    factory, owner_id, item_id, key = _seed(tmp_path)
    data_dir = tmp_path / "data"
    _write_config(data_dir, webhook={"enabled": False}, default_channels=["in_app"])
    future_key = f"{item_id}:future:pending"
    with factory() as db:
        db.add(
            ReminderDelivery(
                todo_item_id=item_id,
                owner_user_id=owner_id,
                scheduled_for=FUTURE,
                channel="pending",
                delivery_key=future_key,
                status="pending",
            )
        )
        db.commit()

    with factory() as db:
        result = deliver_due_reminders(db, data_dir=data_dir, now=NOW)
        db.commit()
        assert result["count"] == 1
        past_row = db.execute(
            select(ReminderDelivery).where(ReminderDelivery.delivery_key == key)
        ).scalar_one()
        future_row = db.execute(
            select(ReminderDelivery).where(
                ReminderDelivery.delivery_key == future_key
            )
        ).scalar_one()
        assert past_row.status == "delivered"
        assert future_row.status == "pending"


def test_webhook_delivery_posts_title_payload(tmp_path):
    factory, _owner_id, _item_id, key = _seed(tmp_path)
    server = _start_server()
    try:
        _host, port = server.server_address
        data_dir = tmp_path / "data"
        _write_config(
            data_dir,
            default_channels=["webhook"],
            webhook={
                "enabled": True,
                "url": f"http://127.0.0.1:{port}/hook",
                "detail_level": "title",
            },
        )
        with factory() as db:
            result = deliver_due_reminders(db, data_dir=data_dir, now=NOW)
            db.commit()
            assert result["count"] == 1
            row = db.execute(
                select(ReminderDelivery).where(ReminderDelivery.delivery_key == key)
            ).scalar_one()
            assert row.status == "delivered"
            assert row.delivered_at is not None
        assert len(server.posts) == 1
        payload = json.loads(server.posts[0]["body"].decode("utf-8"))
        assert payload == {
            "title": "Water the plants",
            "scheduled_for": PAST.isoformat().replace("+00:00", "Z"),
        }
        assert "notes" not in payload
    finally:
        server.shutdown()


def test_idempotent_rescan_does_not_redeliver(tmp_path):
    factory, _owner_id, item_id, key = _seed(tmp_path)
    server = _start_server()
    try:
        _host, port = server.server_address
        data_dir = tmp_path / "data"
        _write_config(
            data_dir,
            default_channels=["in_app", "webhook"],
            webhook={"enabled": True, "url": f"http://127.0.0.1:{port}/hook"},
        )
        with factory() as db:
            first = deliver_due_reminders(db, data_dir=data_dir, now=NOW)
            db.commit()
            second = deliver_due_reminders(db, data_dir=data_dir, now=NOW)
            db.commit()
            assert first["count"] == 1
            assert second["count"] == 0
            events = list(
                db.execute(
                    select(TodoEvent).where(TodoEvent.todo_item_id == item_id)
                ).scalars()
            )
            delivered = [e for e in events if e.event_type == EVENT_DELIVERED]
            channel_ok = [e for e in events if e.event_type == EVENT_CHANNEL_OK]
            assert len(delivered) == 1
            assert len(channel_ok) == 2
            row = db.execute(
                select(ReminderDelivery).where(ReminderDelivery.delivery_key == key)
            ).scalar_one()
            assert row.status == "delivered"
        assert len(server.posts) == 1
    finally:
        server.shutdown()


def test_failure_retries_then_abandons(tmp_path):
    factory, _owner_id, item_id, key = _seed(tmp_path)
    server = _start_server(fail_times=100)
    try:
        _host, port = server.server_address
        data_dir = tmp_path / "data"
        _write_config(
            data_dir,
            max_attempts=3,
            default_channels=["webhook"],
            webhook={"enabled": True, "url": f"http://127.0.0.1:{port}/hook"},
        )
        with factory() as db:
            for _ in range(3):
                deliver_due_reminders(db, data_dir=data_dir, now=NOW)
                db.commit()
            row = db.execute(
                select(ReminderDelivery).where(ReminderDelivery.delivery_key == key)
            ).scalar_one()
            assert row.status == "abandoned"
            events = list(
                db.execute(
                    select(TodoEvent).where(TodoEvent.todo_item_id == item_id)
                ).scalars()
            )
            failures = [e for e in events if e.event_type == EVENT_FAILED]
            abandoned = [e for e in events if e.event_type == EVENT_ABANDONED]
            assert len(failures) == 3
            assert len(abandoned) == 1
        assert len(server.posts) == 3
        with factory() as db:
            deliver_due_reminders(db, data_dir=data_dir, now=NOW)
            db.commit()
        assert len(server.posts) == 3
    finally:
        server.shutdown()


def test_partial_success_does_not_repost_in_app_on_retry(tmp_path):
    factory, _owner_id, item_id, key = _seed(tmp_path)
    server = _start_server(fail_times=1)
    try:
        _host, port = server.server_address
        data_dir = tmp_path / "data"
        _write_config(
            data_dir,
            max_attempts=3,
            default_channels=["in_app", "webhook"],
            webhook={"enabled": True, "url": f"http://127.0.0.1:{port}/hook"},
        )
        with factory() as db:
            first = deliver_due_reminders(db, data_dir=data_dir, now=NOW)
            db.commit()
            assert first["delivered"][0]["action"] == "retry"
            second = deliver_due_reminders(db, data_dir=data_dir, now=NOW)
            db.commit()
            assert second["count"] == 1
            row = db.execute(
                select(ReminderDelivery).where(ReminderDelivery.delivery_key == key)
            ).scalar_one()
            assert row.status == "delivered"
            in_app_ok = [
                e
                for e in db.execute(
                    select(TodoEvent).where(
                        TodoEvent.todo_item_id == item_id,
                        TodoEvent.event_type == EVENT_CHANNEL_OK,
                    )
                ).scalars()
                if json.loads(e.payload_json)["channel"] == "in_app"
            ]
            assert len(in_app_ok) == 1
        assert len(server.posts) == 2
    finally:
        server.shutdown()


def test_config_detail_level_includes_notes(tmp_path):
    factory, _owner_id, _item_id, _key = _seed(tmp_path)
    posts: list[bytes] = []

    def fake_post(url: str, body: bytes, timeout: float):
        del url, timeout
        posts.append(body)
        return 200, ""

    cfg = RemindersConfig(
        enabled=True,
        max_attempts=3,
        default_channels=["webhook"],
        channels={
            "webhook": ChannelConfig(
                enabled=True,
                url="http://127.0.0.1:9/hook",
                detail_level="detail",
            )
        },
    )
    with factory() as db:
        result = deliver_due_reminders(
            db, now=NOW, config=cfg, http_post=fake_post
        )
        db.commit()
        assert result["count"] == 1
    payload = json.loads(posts[0].decode("utf-8"))
    assert payload["notes"] == "balcony shelf"
    assert payload["todo_item_id"] == "todo-20260813-001"
