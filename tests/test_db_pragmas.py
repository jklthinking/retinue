"""Connection pragmas: WAL journalling, foreign keys, and a busy timeout."""

from __future__ import annotations

from sqlalchemy import text

from server.db import make_session_factory


def test_connection_pragmas_are_applied(tmp_path):
    factory = make_session_factory(tmp_path / "pragmas.db")
    with factory() as db:
        assert db.execute(text("PRAGMA journal_mode")).scalar().lower() == "wal"
        assert db.execute(text("PRAGMA foreign_keys")).scalar() == 1
        # Writers serialize even under WAL. A zero timeout would fail the second
        # concurrent write immediately instead of waiting for the write lock.
        assert db.execute(text("PRAGMA busy_timeout")).scalar() >= 5000
