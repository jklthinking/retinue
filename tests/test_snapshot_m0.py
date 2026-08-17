"""Static board snapshot M0: deterministic JSON/HTML fallback files."""

from __future__ import annotations

import datetime as dt
import json
from pathlib import Path

from sqlalchemy import select

from server.db import Actor, Task, make_session_factory
from server.main import main
from server.snapshot import (
    HTML_FILENAME,
    JSON_FILENAME,
    SNAPSHOT_DIRNAME,
    build_board_payload,
    write_board_snapshot,
)

FIXED_NOW = dt.datetime(2026, 8, 13, 12, 0, tzinfo=dt.timezone.utc)
EARLIER = dt.datetime(2026, 8, 12, 9, 30, tzinfo=dt.timezone.utc)


def _seed(data_dir: Path) -> Path:
    db_path = data_dir / "retinue.db"
    factory = make_session_factory(db_path)
    with factory() as db:
        db.add(Actor(id="alice", kind="human", display_name="Alice"))
        db.add(
            Task(
                id="task-20260813-002",
                title="Second card",
                created_by="alice",
                holder="alice",
                status="doing",
                priority="high",
                progress=40,
                created_at=EARLIER,
                updated_at=FIXED_NOW,
            )
        )
        db.add(
            Task(
                id="task-20260813-001",
                title="First card",
                created_by="alice",
                holder="alice",
                status="queued",
                priority="none",
                progress=0,
                created_at=EARLIER,
                updated_at=EARLIER,
            )
        )
        db.commit()
    return db_path


def test_cli_snapshot_writes_stable_files(tmp_path: Path):
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    _seed(data_dir)

    assert main(["--data-dir", str(data_dir), "snapshot"]) == 0

    json_path = data_dir / SNAPSHOT_DIRNAME / JSON_FILENAME
    html_path = data_dir / SNAPSHOT_DIRNAME / HTML_FILENAME
    assert json_path.is_file()
    assert html_path.is_file()

    first_json = json_path.read_bytes()
    first_html = html_path.read_bytes()

    payload = json.loads(first_json.decode("utf-8"))
    assert payload["generated_at"] == "2026-08-13T12:00:00Z"
    assert payload["task_counts"]["queued"] == 1
    assert payload["task_counts"]["doing"] == 1
    assert [row["id"] for row in payload["tasks"]] == [
        "task-20260813-001",
        "task-20260813-002",
    ]
    assert list(payload["tasks"][0].keys()) == sorted(
        ("id", "title", "status", "holder", "progress", "priority")
    )

    html_text = first_html.decode("utf-8")
    assert "<script" not in html_text.lower()
    assert "http://" not in html_text and "https://" not in html_text
    assert "女王" not in html_text and "御前" not in html_text and "宫廷" not in html_text
    assert "Board snapshot" in html_text

    assert main(["--data-dir", str(data_dir), "snapshot"]) == 0
    assert json_path.read_bytes() == first_json
    assert html_path.read_bytes() == first_html


def test_snapshot_readable_without_fastapi(tmp_path: Path):
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    db_path = _seed(data_dir)
    factory = make_session_factory(db_path)
    with factory() as db:
        write_board_snapshot(data_dir, db)

    # Re-open only the files — no app, no ASGI.
    payload = json.loads(
        (data_dir / SNAPSHOT_DIRNAME / JSON_FILENAME).read_text(encoding="utf-8")
    )
    assert payload["tasks"][0]["title"] == "First card"
    html_text = (data_dir / SNAPSHOT_DIRNAME / HTML_FILENAME).read_text(encoding="utf-8")
    assert "First card" in html_text


def test_empty_board_omits_wall_clock_unless_injected(tmp_path: Path):
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    factory = make_session_factory(data_dir / "retinue.db")
    with factory() as db:
        payload = build_board_payload(db)
        assert "generated_at" not in payload
        injected = build_board_payload(db, now=FIXED_NOW)
        assert injected["generated_at"] == "2026-08-13T12:00:00Z"
        write_board_snapshot(data_dir, db)
        write_board_snapshot(data_dir, db)
    raw = (data_dir / SNAPSHOT_DIRNAME / JSON_FILENAME).read_bytes()
    with factory() as db:
        write_board_snapshot(data_dir, db)
    assert (data_dir / SNAPSHOT_DIRNAME / JSON_FILENAME).read_bytes() == raw


def test_snapshot_does_not_mutate_tasks(tmp_path: Path):
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    db_path = _seed(data_dir)
    factory = make_session_factory(db_path)
    with factory() as db:
        before = [
            (row.id, row.status, row.holder, row.progress, row.updated_at.isoformat())
            for row in db.execute(select(Task).order_by(Task.id)).scalars()
        ]
        write_board_snapshot(data_dir, db)
        after = [
            (row.id, row.status, row.holder, row.progress, row.updated_at.isoformat())
            for row in db.execute(select(Task).order_by(Task.id)).scalars()
        ]
    assert before == after
