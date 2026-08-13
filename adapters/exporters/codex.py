"""Read Codex JSONL sessions and emit a compact metrics snapshot."""

from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
import json
import os
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from core.protocol.task import ID_RE, ProtocolError


TOKEN_FIELDS = (
    "input_tokens",
    "cached_input_tokens",
    "output_tokens",
    "reasoning_output_tokens",
    "total_tokens",
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


def _usage(record: dict[str, Any]) -> dict[str, int] | None:
    payload = record.get("payload")
    if record.get("type") != "event_msg" or not isinstance(payload, dict):
        return None
    if payload.get("type") != "token_count":
        return None
    info = payload.get("info")
    if not isinstance(info, dict):
        return None
    raw = info.get("total_token_usage")
    if not isinstance(raw, dict):
        return None
    values: dict[str, int] = {}
    for field in TOKEN_FIELDS:
        value = raw.get(field)
        if not isinstance(value, int) or isinstance(value, bool) or value < 0:
            return None
        values[field] = value
    return values


def _counter_delta(current: dict[str, int], previous: dict[str, int]) -> dict[str, int]:
    """Turn a cumulative snapshot into a delta, tolerating a counter reset."""
    if any(current[field] < previous[field] for field in TOKEN_FIELDS):
        return dict(current)
    return {field: current[field] - previous[field] for field in TOKEN_FIELDS}


def collect_metrics(
    source: Path | str,
    *,
    agent_id: str,
    timezone_name: str | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Collect usage from Codex cumulative token events without modifying sessions."""
    source = Path(source).expanduser().resolve()
    if not source.is_dir():
        raise ProtocolError(f"Codex session directory does not exist: {source}")
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
    transcript_files = 0

    for path in sorted(source.rglob("*.jsonl")):
        transcript_files += 1
        session_id = f"file:{path.relative_to(source)}"
        records: list[tuple[datetime, dict[str, int]]] = []
        active_days: set[date] = set()
        try:
            with path.open(encoding="utf-8", errors="replace") as stream:
                for line in stream:
                    try:
                        record = json.loads(line)
                    except (json.JSONDecodeError, UnicodeDecodeError):
                        invalid_records += 1
                        continue
                    if not isinstance(record, dict):
                        invalid_records += 1
                        continue
                    timestamp = _timestamp(record.get("timestamp"))
                    if timestamp is not None:
                        local_day = timestamp.astimezone(tz).date()
                        if first_day <= local_day <= today:
                            active_days.add(local_day)
                        if last_active is None or timestamp > last_active:
                            last_active = timestamp
                    payload = record.get("payload")
                    if (
                        record.get("type") == "session_meta"
                        and isinstance(payload, dict)
                        and isinstance(payload.get("id"), str)
                        and payload["id"]
                    ):
                        session_id = payload["id"]
                    usage = _usage(record)
                    if usage is not None and timestamp is not None:
                        records.append((timestamp, usage))
        except (OSError, UnicodeDecodeError):
            invalid_records += 1
            continue

        all_sessions.add(session_id)
        for day in active_days:
            sessions_by_day[day].add(session_id)
            sessions_7d.add(session_id)

        previous = {field: 0 for field in TOKEN_FIELDS}
        for timestamp, cumulative in records:
            delta = _counter_delta(cumulative, previous)
            previous = cumulative
            local_day = timestamp.astimezone(tz).date()
            if local_day in buckets:
                for field in TOKEN_FIELDS:
                    buckets[local_day][field] += delta[field]

    daily = [
        {
            "date": day.isoformat(),
            **values,
            "sessions": len(sessions_by_day[day]),
        }
        for day, values in buckets.items()
    ]
    seven_day_tokens = {
        field: sum(item[field] for item in daily) for field in TOKEN_FIELDS
    }
    return {
        "schema_version": 1,
        "agent_id": agent_id,
        "runtime": "codex",
        "generated_at": current.isoformat(timespec="seconds"),
        "timezone": str(getattr(tz, "key", tz)),
        "source": {
            "kind": "codex-jsonl",
            "path": str(source),
            "read_only": True,
            "files": transcript_files,
            "invalid_records": invalid_records,
        },
        "token_accounting": {
            "included_fields": list(TOKEN_FIELDS),
            "total_definition": "Codex upstream total_tokens (input + output)",
            "counter_mode": "deltas of cumulative per-session snapshots",
            "subset_fields": ["cached_input_tokens", "reasoning_output_tokens"],
            "cross_runtime_comparable": False,
        },
        "today": daily[-1],
        "last_7_days": {
            **seven_day_tokens,
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
    """Write one snapshot atomically outside the Codex session directory."""
    source = Path(source).expanduser().resolve()
    destination = Path(destination).expanduser().resolve()
    if destination == source or source in destination.parents:
        raise ProtocolError("metrics destination must be outside the session directory")
    result = collect_metrics(
        source, agent_id=agent_id, timezone_name=timezone_name, now=now
    )
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_suffix(destination.suffix + ".tmp")
    temporary.write_text(
        json.dumps(result, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    os.replace(temporary, destination)
    return result
