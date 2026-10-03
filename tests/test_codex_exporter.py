from datetime import datetime
import json
from zoneinfo import ZoneInfo

import pytest

from adapters.exporters.codex import collect_metrics, export_metrics
from core.protocol.task import ProtocolError


def write_jsonl(path, records, invalid=False):
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [json.dumps(record) for record in records]
    if invalid:
        lines.append("{not-json")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def session(at, session_id):
    return {"type": "session_meta", "timestamp": at, "payload": {"id": session_id}}


def token_count(at, *, input_tokens, cached, output, reasoning):
    return {
        "type": "event_msg",
        "timestamp": at,
        "payload": {
            "type": "token_count",
            "info": {
                "total_token_usage": {
                    "input_tokens": input_tokens,
                    "cached_input_tokens": cached,
                    "output_tokens": output,
                    "reasoning_output_tokens": reasoning,
                    "total_tokens": input_tokens + output,
                }
            },
        },
    }


def test_collects_cumulative_session_deltas_by_local_day(tmp_path):
    source = tmp_path / "sessions"
    write_jsonl(source / "one.jsonl", [
        session("2026-07-19T01:00:00Z", "session-1"),
        token_count("2026-07-19T01:01:00Z", input_tokens=8, cached=3, output=2, reasoning=1),
        token_count("2026-07-19T01:02:00Z", input_tokens=12, cached=5, output=3, reasoning=1),
        token_count("2026-07-20T01:00:00Z", input_tokens=16, cached=6, output=4, reasoning=2),
    ], invalid=True)
    write_jsonl(source / "two.jsonl", [
        session("2026-07-20T02:00:00Z", "session-2"),
        token_count("2026-07-20T02:01:00Z", input_tokens=5, cached=0, output=2, reasoning=0),
    ])

    result = collect_metrics(
        source,
        agent_id="codex-1",
        timezone_name="Asia/Shanghai",
        now=datetime(2026, 7, 20, 12, tzinfo=ZoneInfo("Asia/Shanghai")),
    )

    assert result["today"]["total_tokens"] == 12
    assert result["today"]["sessions"] == 2
    assert result["last_7_days"]["total_tokens"] == 27
    assert result["last_7_days"]["sessions"] == 2
    assert result["sessions"] == 2
    assert result["source"]["invalid_records"] == 1
    assert result["last_active_at"] == "2026-07-20T10:01:00+08:00"
    assert result["token_accounting"]["cross_runtime_comparable"] is False


def test_export_is_atomic_and_does_not_touch_source(tmp_path):
    source = tmp_path / "sessions"
    write_jsonl(source / "one.jsonl", [session("2026-07-20T01:00:00Z", "session-1")])
    before = (source / "one.jsonl").read_bytes()
    destination = tmp_path / "fleet" / "metrics" / "codex-1.json"

    result = export_metrics(
        source,
        destination,
        agent_id="codex-1",
        timezone_name="UTC",
        now=datetime(2026, 7, 20, 12, tzinfo=ZoneInfo("UTC")),
    )

    assert json.loads(destination.read_text()) == result
    assert (source / "one.jsonl").read_bytes() == before
    assert not destination.with_suffix(".json.tmp").exists()


def test_export_refuses_to_write_inside_source(tmp_path):
    source = tmp_path / "sessions"
    write_jsonl(source / "one.jsonl", [])
    with pytest.raises(ProtocolError, match="outside the session directory"):
        export_metrics(source, source / "metrics.json", agent_id="codex-1")


def test_copied_and_resumed_native_session_is_counted_once_without_losing_reset(tmp_path):
    source = tmp_path / "sessions"
    original = [
        session("2026-07-19T23:00:00Z", "session-1"),
        token_count("2026-07-19T23:01:00Z", input_tokens=10, cached=2, output=2, reasoning=1),
        token_count("2026-07-20T00:01:00Z", input_tokens=15, cached=3, output=3, reasoning=1),
    ]
    write_jsonl(source / "original.jsonl", original)
    write_jsonl(source / "copy.jsonl", original + [
        token_count("2026-07-20T00:02:00Z", input_tokens=20, cached=4, output=5, reasoning=2),
        token_count("2026-07-20T00:03:00Z", input_tokens=2, cached=0, output=1, reasoning=0),
    ])
    # Identical counters on a distinct native session are independent work.
    write_jsonl(source / "independent.jsonl", [
        session("2026-07-20T00:00:00Z", "session-2"),
        token_count("2026-07-20T00:01:00Z", input_tokens=2, cached=0, output=1, reasoning=0),
    ])
    before = {path: path.read_bytes() for path in source.rglob("*.jsonl")}
    result = collect_metrics(source, agent_id="codex-1", timezone_name="UTC", now=datetime(2026, 7, 20, 12, tzinfo=ZoneInfo("UTC")))
    assert result["today"]["total_tokens"] == 19  # 13 after midnight + 3 reset + 3 independent.
    assert result["last_7_days"]["total_tokens"] == 31
    assert result["sessions"] == 2
    assert result["source"]["duplicate_usage_snapshots"] == 2
    assert {path: path.read_bytes() for path in source.rglob("*.jsonl")} == before
