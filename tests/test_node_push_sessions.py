from __future__ import annotations

import io
import json
import urllib.error

import pytest

import node.push_sessions as push_sessions


URL = "http://127.0.0.1:9219"


def _http_error(detail: str) -> urllib.error.HTTPError:
    body = io.BytesIO(json.dumps({"detail": detail}).encode("utf-8"))
    return urllib.error.HTTPError(URL, 409, "Conflict", None, body)


def test_push_keeps_server_history_and_continues_after_stale_cursor(monkeypatch):
    outcomes = iter(
        [
            _http_error("stale session cursor"),
            io.BytesIO(b'{"sync_status":"created"}'),
        ]
    )

    def fake_open_url(*args, **kwargs):
        outcome = next(outcomes)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    monkeypatch.setattr(push_sessions, "open_url", fake_open_url)

    result = push_sessions.push(
        URL,
        "synthetic-token",
        [
            {"external_id": "stale-session"},
            {"external_id": "fresh-session"},
        ],
    )

    assert result == {
        "created": 1,
        "updated": 0,
        "unchanged": 0,
        "stale": 1,
    }


def test_push_still_rejects_same_cursor_content_conflict(monkeypatch):
    def fake_open_url(*args, **kwargs):
        raise _http_error("session cursor conflict")

    monkeypatch.setattr(push_sessions, "open_url", fake_open_url)

    with pytest.raises(RuntimeError, match="session cursor conflict"):
        push_sessions.push(
            URL,
            "synthetic-token",
            [{"external_id": "conflicting-session"}],
        )
