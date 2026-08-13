"""Shared test fixtures.

Every test runs with the runtime pin file pointed at a path that does not
exist, so a real pin file on the machine running the suite can never change
a result. Tests that exercise pins override the variable themselves.
"""

from __future__ import annotations

import json
import re
import sqlite3

import pytest

from core.protocol.task import (
    ProtocolError,
    audit_task_card,
    drift_report,
    fold_task_events,
    load_task,
)
from node.runtime_pins import ENV_PINS_FILE


_TASK_FILE = re.compile(r"^task-[0-9]{8}-[0-9]{3}\.ya?ml$")


def _json_object(value):
    try:
        parsed = json.loads(value or "{}")
    except (TypeError, json.JSONDecodeError):
        return value
    return parsed


def _assert_database_task_chains(db_path):
    connection = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }
        if not {"tasks", "task_events"} <= tables:
            return
        rows = connection.execute(
            "SELECT id, priority, acceptance_json, dept, refs_json, progress, "
            "blocked_reason, holder, status FROM tasks"
        ).fetchall()
        for row in rows:
            task_id = row[0]
            raw_events = connection.execute(
                "SELECT from_status, to_status, from_holder, to_holder, payload_json "
                "FROM task_events WHERE task_id = ? ORDER BY seq",
                (task_id,),
            ).fetchall()
            events = [
                {
                    "from_status": event[0],
                    "to_status": event[1],
                    "from_holder": event[2],
                    "to_holder": event[3],
                    "payload": _json_object(event[4]),
                }
                for event in raw_events
            ]
            folded = fold_task_events(events)
            first_payload = events[0]["payload"] if events else {}
            modern = (
                isinstance(first_payload, dict)
                and first_payload.get("state_version") == 1
            )
            if modern:
                stored = {
                    "priority": row[1],
                    "acceptance": json.loads(row[2]),
                    "dept": row[3],
                    "refs": json.loads(row[4]),
                    "progress": row[5],
                    "blocked_reason": row[6],
                    "holder": row[7],
                    "status": row[8],
                }
                report = drift_report(folded, stored)
                assert report["status"] == "in_sync", (
                    f"modern task chain disagrees with its row: {task_id}: {report}"
                )
            elif events:
                assert not folded.reconstructible, (
                    f"legacy task chain was treated as complete: {task_id}"
                )
    finally:
        connection.close()


def _assert_file_task_chains(root):
    for path in root.rglob("*.y*ml"):
        if not _TASK_FILE.fullmatch(path.name):
            continue
        try:
            task = load_task(path)
        except ProtocolError:
            continue
        first_payload = task["chain"][0].get("payload", {}) if task["chain"] else {}
        if first_payload.get("state_version") == 1:
            report = audit_task_card(task)
            assert report["status"] == "in_sync", (
                f"modern file task chain disagrees with its card: {task['id']}: {report}"
            )
        elif task["chain"]:
            assert not audit_task_card(task)["fold"]["reconstructible"], (
                f"legacy file task chain was treated as complete: {task['id']}"
            )


@pytest.fixture(autouse=True)
def _no_runtime_pins(monkeypatch, tmp_path):
    monkeypatch.setenv(ENV_PINS_FILE, str(tmp_path / "no-runtime-pins.json"))
    yield
    for db_path in tmp_path.rglob("*.db"):
        _assert_database_task_chains(db_path)
    _assert_file_task_chains(tmp_path)
