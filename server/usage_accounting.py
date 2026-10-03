"""Read-model helpers for reported usage; never infer unrecorded consumption."""
from __future__ import annotations

import datetime as dt
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

STALE_AFTER_SECONDS = 30 * 60


def utc_now() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


def calendar_timezone(name: str) -> dt.tzinfo:
    # These two daily calendars work on Windows without an optional tzdata
    # package. Shanghai has used UTC+8 without seasonal changes since 1992.
    if name == "UTC":
        return dt.timezone.utc
    if name == "Asia/Shanghai":
        return dt.timezone(dt.timedelta(hours=8), name)
    if name == "local":
        return dt.datetime.now().astimezone().tzinfo or dt.timezone.utc
    try:
        return ZoneInfo(name)
    except (ValueError, ZoneInfoNotFoundError) as exc:
        raise ValueError("unsupported calendar timezone") from exc


def report_freshness(value: dt.datetime | None, now: dt.datetime) -> dict:
    if value is None:
        return {"last_reported_at": None, "freshness_seconds": None, "stale": None}
    if value.tzinfo is None:
        value = value.replace(tzinfo=dt.timezone.utc)
    age = max(0, int((now - value).total_seconds()))
    return {
        "last_reported_at": value.astimezone(dt.timezone.utc).isoformat(),
        "freshness_seconds": age,
        "stale": age > STALE_AFTER_SECONDS,
    }


def input_definition(runtime: str) -> str:
    if runtime == "claude-code":
        return "input + cache_creation_input + cache_read_input"
    if runtime == "codex":
        return "input (cached input is an included subset)"
    return "reporter-provided input; cache breakdown unavailable"
