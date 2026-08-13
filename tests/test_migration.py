"""Schema migration: old databases gain the new columns with correct backfill."""

from __future__ import annotations

import sqlite3

from sqlalchemy import select

from server.db import Task, make_session_factory, migrate_database

OLD_TASKS_DDL = """
CREATE TABLE tasks (
    id VARCHAR(32) NOT NULL PRIMARY KEY,
    title VARCHAR(256) NOT NULL,
    created_by VARCHAR(64) NOT NULL,
    dept VARCHAR(64),
    priority VARCHAR(16) NOT NULL,
    status VARCHAR(16) NOT NULL,
    holder VARCHAR(64) NOT NULL,
    blocked_reason TEXT,
    next_holder VARCHAR(64),
    acceptance_json TEXT NOT NULL,
    refs_json TEXT NOT NULL,
    archived BOOLEAN NOT NULL,
    created_at DATETIME NOT NULL,
    updated_at DATETIME NOT NULL
)
"""


def test_old_schema_gains_columns_and_done_backfill(tmp_path):
    db_path = tmp_path / "old.db"
    raw = sqlite3.connect(db_path)
    raw.execute(OLD_TASKS_DDL)
    for task_id, status in [("task-20260101-001", "done"), ("task-20260101-002", "doing")]:
        raw.execute(
            "INSERT INTO tasks VALUES (?, 't', 'a', NULL, 'none', ?, 'a', NULL, NULL,"
            " '[]', '[]', 0, '2026-01-01', '2026-01-01')",
            (task_id, status),
        )
    raw.commit()
    raw.close()

    migrate_database(db_path)
    factory = make_session_factory(db_path)
    with factory() as db:
        tasks = {t.id: t for t in db.execute(select(Task)).scalars()}
        assert tasks["task-20260101-001"].progress == 100  # done rows backfilled
        assert tasks["task-20260101-002"].progress == 0
        assert tasks["task-20260101-001"].open_dispatch is False

    # Idempotent: an explicit second migration must not fail or re-alter.
    migrate_database(db_path)
    with make_session_factory(db_path)() as db:
        assert db.execute(select(Task)).scalars().first() is not None
