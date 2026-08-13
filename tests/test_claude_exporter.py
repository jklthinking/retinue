from datetime import datetime
import json
from zoneinfo import ZoneInfo

import pytest

from adapters.exporters.claude_code import collect_metrics, export_metrics
from core.protocol.task import ProtocolError


def write_jsonl(path, records, invalid=False):
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [json.dumps(record) for record in records]
    if invalid:
        lines.append("{not-json")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def assistant(at, session, message, **usage):
    return {
        "type": "assistant",
        "timestamp": at,
        "sessionId": session,
        "message": {"id": message, "usage": usage},
    }


def test_collects_seven_days_and_deduplicates_message_events(tmp_path):
    source = tmp_path / ".claude" / "projects"
    usage = dict(input_tokens=3, cache_creation_input_tokens=5, cache_read_input_tokens=7, output_tokens=11)
    write_jsonl(source / "project-a" / "one.jsonl", [
        assistant("2026-07-20T01:00:00Z", "session-1", "msg-1", **usage),
        assistant("2026-07-20T01:00:05Z", "session-1", "msg-1", **usage),
        assistant("2026-07-14T02:00:00Z", "session-2", "msg-2", input_tokens=1, cache_creation_input_tokens=0, cache_read_input_tokens=0, output_tokens=2),
        assistant("2026-07-13T02:00:00Z", "old", "msg-old", input_tokens=100, cache_creation_input_tokens=0, cache_read_input_tokens=0, output_tokens=0),
    ], invalid=True)

    result = collect_metrics(
        source,
        agent_id="claude-1",
        timezone_name="Asia/Shanghai",
        now=datetime(2026, 7, 20, 12, tzinfo=ZoneInfo("Asia/Shanghai")),
    )

    assert result["today"]["total_tokens"] == 26
    assert result["last_7_days"]["total_tokens"] == 29
    assert result["last_7_days"]["sessions"] == 2
    assert result["sessions"] == 3
    assert result["source"]["invalid_records"] == 1
    assert result["last_active_at"] == "2026-07-20T09:00:05+08:00"


def test_export_writes_snapshot_outside_source(tmp_path):
    source = tmp_path / "source"
    write_jsonl(source / "one.jsonl", [
        {"timestamp": "2026-07-20T01:00:00Z", "sessionId": "session-1"}
    ])
    destination = tmp_path / "fleet" / "metrics" / "claude-1.json"
    result = export_metrics(
        source,
        destination,
        agent_id="claude-1",
        timezone_name="UTC",
        now=datetime(2026, 7, 20, 12, tzinfo=ZoneInfo("UTC")),
    )
    assert json.loads(destination.read_text()) == result
    assert list(source.rglob("*")) == [source / "one.jsonl"]


def test_export_refuses_to_write_inside_source(tmp_path):
    source = tmp_path / "source"
    write_jsonl(source / "one.jsonl", [])
    with pytest.raises(ProtocolError, match="outside the transcript directory"):
        export_metrics(source, source / "metrics.json", agent_id="claude-1")
