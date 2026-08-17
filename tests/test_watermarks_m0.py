"""Node health watermark policy M0: thresholds, idempotent cards, heartbeat field."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from server.app import create_app
from server.db import Actor, Task, User, make_session_factory
from server.security import hash_password
from server.watermarks import (
    DiskThresholds,
    LoadThresholds,
    WatermarksConfig,
    classify_disk,
    classify_load,
    compute_watermark,
    dedupe_ref,
    evaluate_and_maybe_open_card,
    find_open_watermark_card,
)


def _write_config(data_dir: Path, *, enabled: bool = True, actor: str = "retinue-watch") -> None:
    (data_dir / "watermarks.yaml").write_text(
        "\n".join(
            [
                f"enabled: {'true' if enabled else 'false'}",
                f"actor: {actor}",
                "disk:",
                "  warn: 80",
                "  high: 90",
                "  critical: 95",
            ]
        )
        + "\n",
        encoding="utf-8",
    )


@pytest.fixture()
def board(tmp_path: Path):
    factory = make_session_factory(tmp_path / "test.db")
    with factory() as db:
        db.add(Actor(id="owner", kind="human", display_name="Owner"))
        db.add(Actor(id="retinue-watch", kind="agent", display_name="Retinue Watch"))
        db.add(
            User(
                username="owner",
                password_hash=hash_password("owner-pass-123"),
                role="admin",
                actor_id="owner",
            )
        )
        db.commit()
    client = TestClient(create_app(factory, data_dir=tmp_path))
    return client, factory, tmp_path


def _login(client: TestClient) -> None:
    assert (
        client.post(
            "/api/auth/login", json={"username": "owner", "password": "owner-pass-123"}
        ).status_code
        == 200
    )


def _node_headers(client: TestClient, node_id: str) -> dict[str, str]:
    issued = client.post("/api/admin/node-tokens", json={"node_id": node_id})
    assert issued.status_code == 200
    return {"Authorization": f"Bearer {issued.json()['token']}"}


def test_disk_threshold_boundaries():
    thr = DiskThresholds(warn=80, high=90, critical=95)
    assert classify_disk({"percent": 79.9}, thr) == "ok"
    assert classify_disk({"percent": 80}, thr) == "warn"
    assert classify_disk({"percent": 89.9}, thr) == "warn"
    assert classify_disk({"percent": 90}, thr) == "high"
    assert classify_disk({"percent": 94.9}, thr) == "high"
    assert classify_disk({"percent": 95}, thr) == "critical"
    assert classify_disk({"percent": 99}, thr) == "critical"


def test_legacy_disk_without_percent_is_unknown():
    thr = DiskThresholds()
    assert classify_disk({"used": 1, "total": 2}, thr) == "unknown"
    assert classify_disk({}, thr) == "unknown"
    assert classify_disk(None, thr) == "unknown"
    wm = compute_watermark({"used": 10}, [], WatermarksConfig())
    assert wm == {"disk": "unknown", "load": "unknown"}


def test_load_skipped_when_unconfigured_or_missing():
    assert classify_load([9.0], LoadThresholds()) == "unknown"
    cfg = WatermarksConfig(
        load=LoadThresholds(warn=4.0, high=8.0, critical=16.0),
    )
    assert classify_load([], cfg.load) == "unknown"
    assert classify_load(None, cfg.load) == "unknown"
    assert classify_load([3.9], cfg.load) == "ok"
    assert classify_load([4.0], cfg.load) == "warn"
    assert classify_load([8.0], cfg.load) == "high"
    assert classify_load([16.0], cfg.load) == "critical"


def test_enabled_false_is_noop_for_cards(board):
    client, factory, data_dir = board
    _write_config(data_dir, enabled=False)
    with factory() as db:
        before = db.execute(select(Task)).scalars().all()
        assert before == []
        wm = evaluate_and_maybe_open_card(
            db,
            node_id="node-a",
            disk={"percent": 96},
            load=[],
            data_dir=data_dir,
        )
        db.commit()
        assert wm["disk"] == "critical"
        assert db.execute(select(Task)).scalars().all() == []


def test_same_tier_does_not_reopen_card(board):
    client, factory, data_dir = board
    _write_config(data_dir, enabled=True)
    with factory() as db:
        first = evaluate_and_maybe_open_card(
            db,
            node_id="node-a",
            disk={"percent": 91},
            load=[],
            data_dir=data_dir,
        )
        db.commit()
        assert first["disk"] == "high"
        tasks = list(db.execute(select(Task)).scalars())
        assert len(tasks) == 1
        assert dedupe_ref("node-a", "high") in tasks[0].refs
        assert find_open_watermark_card(db, "node-a", "high") is not None

        evaluate_and_maybe_open_card(
            db,
            node_id="node-a",
            disk={"percent": 92},
            load=[],
            data_dir=data_dir,
        )
        db.commit()
        tasks = list(db.execute(select(Task)).scalars())
        assert len(tasks) == 1


def test_missing_actor_records_level_without_card(board):
    client, factory, data_dir = board
    _write_config(data_dir, enabled=True, actor="missing-watch")
    with factory() as db:
        wm = evaluate_and_maybe_open_card(
            db,
            node_id="node-a",
            disk={"percent": 97},
            load=[],
            data_dir=data_dir,
        )
        db.commit()
        assert wm["disk"] == "critical"
        assert db.execute(select(Task)).scalars().all() == []


def test_heartbeat_returns_watermark_and_list_exposes_it(board):
    client, factory, data_dir = board
    _write_config(data_dir, enabled=True)
    _login(client)
    headers = _node_headers(client, "node-a")
    beat = {
        "id": "node-a",
        "hostname": "synthetic-host",
        "disk": {"total": 100, "used": 93, "percent": 93},
        "load": [0.5, 0.4, 0.3],
    }
    response = client.post("/api/nodes/heartbeat", json=beat, headers=headers)
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["watermark"]["disk"] == "high"
    assert body["watermark"]["load"] == "unknown"

    nodes = client.get("/api/nodes").json()
    node = next(item for item in nodes if item["id"] == "node-a")
    assert node["watermark"]["disk"] == "high"
    assert node["watermark"]["load"] == "unknown"

    with factory() as db:
        tasks = list(db.execute(select(Task)).scalars())
        assert len(tasks) == 1
        assert tasks[0].title == "节点磁盘告警 node-a"


def test_legacy_heartbeat_without_percent_stays_unknown(board):
    client, factory, data_dir = board
    _write_config(data_dir, enabled=True)
    _login(client)
    headers = _node_headers(client, "node-b")
    response = client.post(
        "/api/nodes/heartbeat",
        json={"id": "node-b", "disk": {"used": 1, "total": 2}},
        headers=headers,
    )
    assert response.status_code == 200
    assert response.json()["watermark"]["disk"] == "unknown"
    with factory() as db:
        assert db.execute(select(Task)).scalars().all() == []


def test_warn_does_not_open_card(board):
    client, factory, data_dir = board
    _write_config(data_dir, enabled=True)
    with factory() as db:
        wm = evaluate_and_maybe_open_card(
            db,
            node_id="node-a",
            disk={"percent": 85},
            load=[],
            data_dir=data_dir,
        )
        db.commit()
        assert wm["disk"] == "warn"
        assert db.execute(select(Task)).scalars().all() == []
