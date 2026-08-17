"""QC completion hook M0: fire-and-forget notify on done, never blocks completion."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

from server import qc_hook
from server.db import Actor, Task, make_session_factory
from server.engine import create_task, update_task
from server.qc_hook import (
    QcHookConfig,
    bind_data_dir,
    load_qc_hook_config,
    maybe_notify_task_done,
    reset_memory_dedupe,
)


def _write_config(data_dir: Path, **overrides) -> None:
    raw = {
        "enabled": True,
        "url_env": "RETINUE_QC_HOOK_URL",
        "timeout_seconds": 2,
    }
    raw.update(overrides)
    data_dir.mkdir(parents=True, exist_ok=True)
    (data_dir / "qc_hook.yaml").write_text(
        yaml.safe_dump(raw, sort_keys=False), encoding="utf-8"
    )


def _seed(tmp_path: Path):
    factory = make_session_factory(tmp_path / "qc.db")
    with factory() as db:
        db.add(Actor(id="worker", kind="agent", display_name="Worker"))
        db.flush()
        task = create_task(
            db,
            title="Ship the widget",
            created_by="worker",
            holder="worker",
        )
        db.commit()
        return factory, task.id


@pytest.fixture(autouse=True)
def _clean_qc_state(tmp_path, monkeypatch):
    reset_memory_dedupe()
    bind_data_dir(tmp_path / "data")
    monkeypatch.delenv("RETINUE_QC_HOOK_URL", raising=False)
    yield
    bind_data_dir(None)
    reset_memory_dedupe()


def test_load_config_missing_file_is_inert(tmp_path):
    cfg = load_qc_hook_config(tmp_path / "missing")
    assert cfg.enabled is False


def test_done_fires_hook_once(tmp_path, monkeypatch):
    data_dir = tmp_path / "data"
    _write_config(data_dir)
    monkeypatch.setenv("RETINUE_QC_HOOK_URL", "http://qc.test.invalid/hook")
    posts: list[tuple[str, dict, float]] = []

    def fake_post(url: str, body: bytes, timeout: float):
        posts.append((url, json.loads(body.decode("utf-8")), timeout))
        return 200, ""

    monkeypatch.setattr(qc_hook, "_default_http_post", fake_post)

    factory, task_id = _seed(tmp_path)
    with factory() as db:
        task = db.get(Task, task_id)
        assert task is not None
        update_task(
            db,
            task,
            who="worker",
            is_privileged=True,
            status="doing",
            note="start work",
        )
        update_task(
            db,
            task,
            who="worker",
            is_privileged=True,
            status="done",
            note="finished",
        )
        db.commit()
        assert task.status == "done"

    assert len(posts) == 1
    url, payload, timeout = posts[0]
    assert url == "http://qc.test.invalid/hook"
    assert payload["task_id"] == task_id
    assert payload["title"] == "Ship the widget"
    assert payload["holder"] == "worker"
    assert "done_at" in payload
    assert timeout == 2


def test_hook_error_does_not_block_done(tmp_path, monkeypatch):
    data_dir = tmp_path / "data"
    _write_config(data_dir)
    monkeypatch.setenv("RETINUE_QC_HOOK_URL", "http://qc.test.invalid/hook")

    def boom(url: str, body: bytes, timeout: float):
        raise RuntimeError("network down")

    monkeypatch.setattr(qc_hook, "_default_http_post", boom)

    factory, task_id = _seed(tmp_path)
    with factory() as db:
        task = db.get(Task, task_id)
        assert task is not None
        update_task(
            db,
            task,
            who="worker",
            is_privileged=True,
            status="doing",
            note="start work",
        )
        update_task(
            db,
            task,
            who="worker",
            is_privileged=True,
            status="done",
            note="finished despite hook",
        )
        db.commit()
        assert task.status == "done"


def test_same_card_not_resent(tmp_path, monkeypatch):
    data_dir = tmp_path / "data"
    _write_config(data_dir)
    monkeypatch.setenv("RETINUE_QC_HOOK_URL", "http://qc.test.invalid/hook")
    posts: list[bytes] = []

    def fake_post(url: str, body: bytes, timeout: float):
        posts.append(body)
        return 200, ""

    factory, task_id = _seed(tmp_path)
    with factory() as db:
        task = db.get(Task, task_id)
        assert task is not None
        first = maybe_notify_task_done(task, data_dir=data_dir, http_post=fake_post)
        second = maybe_notify_task_done(task, data_dir=data_dir, http_post=fake_post)
    assert first == "sent"
    assert second == "duplicate"
    assert len(posts) == 1


def test_disabled_does_not_post(tmp_path, monkeypatch):
    data_dir = tmp_path / "data"
    _write_config(data_dir, enabled=False)
    monkeypatch.setenv("RETINUE_QC_HOOK_URL", "http://qc.test.invalid/hook")
    posts: list[bytes] = []

    def fake_post(url: str, body: bytes, timeout: float):
        posts.append(body)
        return 200, ""

    factory, task_id = _seed(tmp_path)
    with factory() as db:
        task = db.get(Task, task_id)
        assert task is not None
        action = maybe_notify_task_done(
            task,
            data_dir=data_dir,
            http_post=fake_post,
            config=QcHookConfig(enabled=False, url_env="RETINUE_QC_HOOK_URL"),
        )
    assert action == "disabled"
    assert posts == []
