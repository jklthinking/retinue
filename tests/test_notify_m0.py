"""Notifier plugin layer M0: channels, idempotency, credential gate, refresh, v20."""

from __future__ import annotations

import json
import logging
from pathlib import Path

import yaml
from sqlalchemy import select, text

from server.db import (
    LATEST_SCHEMA_VERSION,
    NotificationDelivery,
    make_session_factory,
    migrate_database,
)
from server.notify import (
    CHANNEL_GROUP_WEBHOOK,
    CHANNEL_LOG,
    CHANNEL_TENANT_APP,
    ChannelConfig,
    NotifyConfig,
    deliver,
    load_notify_config,
    refresh_after_approval,
    resolve_channel_name,
    send,
)


def _write_config(data_dir: Path, **overrides) -> None:
    raw = {
        "default_channel": "group_webhook",
        "channels": {
            "group_webhook": {
                "enabled": True,
                "webhook_url_env": "RETINUE_FEISHU_WEBHOOK",
                "timeout_seconds": 2,
            },
            "tenant_app": {
                "enabled": True,
                "app_id": "cli_test_app",
                "app_secret_env": "RETINUE_FEISHU_APP_SECRET",
                "api_base": "https://open.feishu.cn",
                "receive_id_type": "chat_id",
                "timeout_seconds": 2,
            },
            "log": {"enabled": True},
        },
    }
    if "default_channel" in overrides:
        raw["default_channel"] = overrides.pop("default_channel")
    for key, value in overrides.items():
        if key in raw["channels"] and isinstance(value, dict):
            raw["channels"][key].update(value)
        else:
            raw[key] = value
    data_dir.mkdir(parents=True, exist_ok=True)
    (data_dir / "notify.yaml").write_text(
        yaml.safe_dump(raw, sort_keys=False), encoding="utf-8"
    )


def test_plugin_selection_follows_config(tmp_path, monkeypatch):
    data_dir = tmp_path / "data"
    _write_config(data_dir, default_channel="log")
    cfg = load_notify_config(data_dir)
    assert resolve_channel_name(cfg) == CHANNEL_LOG

    _write_config(data_dir, default_channel="tenant_app")
    cfg = load_notify_config(data_dir)
    assert resolve_channel_name(cfg) == CHANNEL_TENANT_APP

    monkeypatch.delenv("RETINUE_FEISHU_WEBHOOK", raising=False)
    cfg = NotifyConfig(default_channel="")
    assert resolve_channel_name(cfg) == CHANNEL_LOG
    monkeypatch.setenv("RETINUE_FEISHU_WEBHOOK", "http://127.0.0.1:9/hook")
    assert resolve_channel_name(cfg) == CHANNEL_GROUP_WEBHOOK


def test_idempotent_deliver_same_dedupe_key_does_not_resend(tmp_path):
    factory = make_session_factory(tmp_path / "notify.db")
    posts: list[tuple[str, bytes]] = []

    def http_client(method, url, body, headers, timeout):
        del method, headers, timeout
        posts.append((url, body or b""))
        return 200, b'{"ok":true}'

    cfg = NotifyConfig(
        default_channel=CHANNEL_GROUP_WEBHOOK,
        channels={
            CHANNEL_GROUP_WEBHOOK: ChannelConfig(
                enabled=True,
                webhook_url_env="RETINUE_FEISHU_WEBHOOK",
            )
        },
    )
    payload = {"msg_type": "text", "content": {"text": "hello"}}
    with factory() as db:
        first = deliver(
            db,
            dedupe_key="card-1:receipt",
            target="http://127.0.0.1:9/hook",
            payload=payload,
            config=cfg,
            http_client=http_client,
        )
        db.commit()
        second = deliver(
            db,
            dedupe_key="card-1:receipt",
            target="http://127.0.0.1:9/hook",
            payload=payload,
            config=cfg,
            http_client=http_client,
        )
        db.commit()
        assert first["action"] == "delivered"
        assert second["action"] == "skipped"
        assert second["reason"] == "already_terminal"
        row = db.execute(
            select(NotificationDelivery).where(
                NotificationDelivery.dedupe_key == "card-1:receipt"
            )
        ).scalar_one()
        assert row.status == "delivered"
        assert row.attempts == 1
    assert len(posts) == 1


def test_credential_gate_degrades_to_log(tmp_path, caplog, monkeypatch):
    monkeypatch.delenv("RETINUE_FEISHU_APP_SECRET", raising=False)
    data_dir = tmp_path / "data"
    _write_config(data_dir, default_channel="tenant_app")
    cfg = load_notify_config(data_dir)
    calls: list[str] = []

    def http_client(method, url, body, headers, timeout):
        del method, body, headers, timeout
        calls.append(url)
        return 200, b"{}"

    with caplog.at_level(logging.WARNING, logger="server.notify"):
        result = send(
            "oc_chat_1",
            {"msg_type": "text", "content": {"text": "hi"}},
            config=cfg,
            http_client=http_client,
        )
    assert result.status == "degraded"
    assert result.channel == CHANNEL_TENANT_APP
    assert calls == []
    assert any("credentials missing" in r.message for r in caplog.records)


def test_tenant_app_send_and_refresh_with_http_stub(tmp_path, monkeypatch):
    monkeypatch.setenv("RETINUE_FEISHU_APP_SECRET", "test-secret-not-real")
    factory = make_session_factory(tmp_path / "tenant.db")
    data_dir = tmp_path / "data"
    _write_config(
        data_dir,
        default_channel="tenant_app",
        tenant_app={
            "enabled": True,
            "app_id": "cli_test_app",
            "app_secret_env": "RETINUE_FEISHU_APP_SECRET",
            "api_base": "https://open.feishu.cn",
        },
    )
    cfg = load_notify_config(data_dir)
    seen: list[dict] = []

    def http_client(method, url, body, headers, timeout):
        del timeout
        seen.append(
            {
                "method": method,
                "url": url,
                "body": body.decode("utf-8") if body else "",
                "headers": dict(headers),
            }
        )
        if "tenant_access_token" in url:
            return 200, json.dumps(
                {"code": 0, "tenant_access_token": "tok-test", "expire": 7200}
            ).encode("utf-8")
        if "/im/v1/messages" in url:
            assert headers.get("Authorization") == "Bearer tok-test"
            return 200, json.dumps(
                {"code": 0, "data": {"message_id": "om_msg_123"}}
            ).encode("utf-8")
        return 404, b"{}"

    with factory() as db:
        outcome = deliver(
            db,
            dedupe_key="approval-9:decision-card",
            target="oc_chat_abc",
            payload={"msg_type": "text", "content": {"text": "please decide"}},
            config=cfg,
            http_client=http_client,
        )
        db.commit()
        assert outcome["action"] == "delivered"
        assert outcome["message_ref"] == "om_msg_123"
        assert outcome["channel"] == CHANNEL_TENANT_APP

        refresh = refresh_after_approval(
            db,
            {
                "dedupe_key": "approval-9:decision-card",
                "decision": "approve",
                "approval_id": 9,
            },
            config=cfg,
            http_client=http_client,
        )
        db.commit()
        assert refresh["action"] == "stubbed"
        assert refresh["ok"] is True
        assert refresh["message_ref"] == "om_msg_123"
        assert refresh["channel"] == CHANNEL_TENANT_APP

    token_calls = [c for c in seen if "tenant_access_token" in c["url"]]
    msg_calls = [c for c in seen if "/im/v1/messages" in c["url"]]
    assert len(token_calls) == 1
    assert len(msg_calls) == 1
    body = json.loads(msg_calls[0]["body"])
    assert body["receive_id"] == "oc_chat_abc"
    assert body["msg_type"] == "text"


def test_migration_v18_to_v20(tmp_path):
    """Fresh DB is v20; an explicit downgrade-to-18 stamp then migrate reaches 20."""
    db_path = tmp_path / "migrate.db"
    factory = make_session_factory(db_path)
    assert LATEST_SCHEMA_VERSION == 20
    with factory() as db:
        version = db.execute(text("SELECT version FROM schema_version WHERE id = 1")).scalar_one()
        assert version == 20
        # Table exists on the fresh schema.
        db.execute(text("SELECT dedupe_key FROM notification_deliveries LIMIT 0"))
        db.commit()

    # Simulate a v18 database that already has todo tables but not notification_deliveries:
    # drop the new table and stamp version 18, then migrate forward.
    import sqlite3

    raw = sqlite3.connect(db_path)
    try:
        raw.execute("DROP TABLE notification_deliveries")
        raw.execute("UPDATE schema_version SET version = 18 WHERE id = 1")
        raw.commit()
    finally:
        raw.close()

    result = migrate_database(db_path)
    assert (result.from_version, result.to_version) == (18, 20)
    factory2 = make_session_factory(db_path)
    with factory2() as db:
        version = db.execute(text("SELECT version FROM schema_version WHERE id = 1")).scalar_one()
        assert version == 20
        db.execute(text("SELECT dedupe_key, message_ref, attempts FROM notification_deliveries LIMIT 0"))
        db.commit()
