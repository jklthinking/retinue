"""Read Claude Code JSONL transcripts and emit a compact metrics snapshot."""

from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
import json
import os
from pathlib import Path
from typing import Any, Iterable
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from core.protocol.task import ID_RE, ProtocolError


TOKEN_FIELDS = (
    "input_tokens",
    "cache_creation_input_tokens",
    "cache_read_input_tokens",
    "output_tokens",
)


def _timezone(name: str | None):
    if name:
        try:
            return ZoneInfo(name)
        except ZoneInfoNotFoundError as exc:
            raise ProtocolError(f"unknown timezone: {name}") from exc
    return datetime.now().astimezone().tzinfo or timezone.utc


def _timestamp(value: Any) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def _tokens(usage: Any) -> dict[str, int] | None:
    if not isinstance(usage, dict):
        return None
    values: dict[str, int] = {}
    for field in TOKEN_FIELDS:
        value = usage.get(field, 0)
        if not isinstance(value, int) or isinstance(value, bool) or value < 0:
            return None
        values[field] = value
    return values


def _iter_records(source: Path) -> Iterable[tuple[Path, int, dict[str, Any] | None]]:
    for path in sorted(source.rglob("*.jsonl")):
        try:
            with path.open(encoding="utf-8", errors="replace") as stream:
                for number, line in enumerate(stream, 1):
                    try:
                        record = json.loads(line)
                    except (json.JSONDecodeError, UnicodeDecodeError):
                        yield path, number, None
                        continue
                    yield path, number, record if isinstance(record, dict) else None
        except (OSError, UnicodeDecodeError):
            yield path, 0, None


def collect_metrics(
    source: Path | str,
    *,
    agent_id: str,
    timezone_name: str | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Collect deduplicated usage without writing to the Claude data directory."""
    source = Path(source).expanduser().resolve()
    if not source.is_dir():
        raise ProtocolError(f"Claude Code transcript directory does not exist: {source}")
    if not ID_RE.fullmatch(agent_id):
        raise ProtocolError("agent id must use lowercase letters, digits, and hyphens")

    tz = _timezone(timezone_name)
    current = (now or datetime.now(tz)).astimezone(tz)
    today = current.date()
    first_day = today - timedelta(days=6)
    buckets = {
        first_day + timedelta(days=offset): {field: 0 for field in TOKEN_FIELDS}
        for offset in range(7)
    }
    sessions_by_day: dict[date, set[str]] = defaultdict(set)
    sessions_7d: set[str] = set()
    all_sessions: set[str] = set()
    last_active: datetime | None = None
    invalid_records = 0
    transcript_files: set[Path] = set()
    messages: dict[tuple[str, str], tuple[datetime, dict[str, int]]] = {}

    for path, number, record in _iter_records(source):
        transcript_files.add(path)
        if record is None:
            invalid_records += 1
            continue
        timestamp = _timestamp(record.get("timestamp"))
        session_id = record.get("sessionId") or record.get("session_id")
        if not isinstance(session_id, str) or not session_id:
            session_id = f"file:{path.relative_to(source)}"
        all_sessions.add(session_id)
        if timestamp is not None:
            local_time = timestamp.astimezone(tz)
            if last_active is None or timestamp > last_active:
                last_active = timestamp
            if first_day <= local_time.date() <= today:
                sessions_by_day[local_time.date()].add(session_id)
                sessions_7d.add(session_id)

        message = record.get("message") if isinstance(record.get("message"), dict) else {}
        usage = _tokens(message.get("usage") or record.get("usage"))
        if usage is None or timestamp is None:
            continue
        message_id = message.get("id") or record.get("messageId") or record.get("uuid")
        if not isinstance(message_id, str) or not message_id:
            message_id = f"{path.relative_to(source)}:{number}"
        key = (session_id, message_id)
        previous = messages.get(key)
        if previous is None:
            messages[key] = (timestamp, usage)
        else:
            first_timestamp, previous_usage = previous
            messages[key] = (
                min(first_timestamp, timestamp),
                {field: max(previous_usage[field], usage[field]) for field in TOKEN_FIELDS},
            )

    for timestamp, usage in messages.values():
        local_day = timestamp.astimezone(tz).date()
        if local_day in buckets:
            for field in TOKEN_FIELDS:
                buckets[local_day][field] += usage[field]

    daily = []
    for day, values in buckets.items():
        daily.append({
            "date": day.isoformat(),
            **values,
            "total_tokens": sum(values.values()),
            "sessions": len(sessions_by_day[day]),
        })

    seven_day_tokens = {
        field: sum(item[field] for item in daily) for field in TOKEN_FIELDS
    }
    return {
        "schema_version": 1,
        "agent_id": agent_id,
        "runtime": "claude-code",
        "generated_at": current.isoformat(timespec="seconds"),
        "timezone": str(getattr(tz, "key", tz)),
        "source": {
            "kind": "claude-code-jsonl",
            "path": str(source),
            "read_only": True,
            "files": len(transcript_files),
            "invalid_records": invalid_records,
        },
        "token_accounting": {
            "included_fields": list(TOKEN_FIELDS),
            "total_definition": "input + cache_creation_input + cache_read_input + output",
            "dedupe_key": "session_id + message.id; component-wise max for repeats",
        },
        "today": {**daily[-1]},
        "last_7_days": {
            **seven_day_tokens,
            "total_tokens": sum(seven_day_tokens.values()),
            "sessions": len(sessions_7d),
            "daily": daily,
        },
        "sessions": len(all_sessions),
        "last_active_at": last_active.astimezone(tz).isoformat() if last_active else None,
    }


def export_metrics(
    source: Path | str,
    destination: Path | str,
    *,
    agent_id: str,
    timezone_name: str | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Write one snapshot atomically outside the runtime's transcript directory."""
    source = Path(source).expanduser().resolve()
    destination = Path(destination).expanduser().resolve()
    if destination == source or source in destination.parents:
        raise ProtocolError("metrics destination must be outside the transcript directory")
    result = collect_metrics(
        source, agent_id=agent_id, timezone_name=timezone_name, now=now
    )
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_suffix(destination.suffix + ".tmp")
    temporary.write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    os.replace(temporary, destination)
    return result
