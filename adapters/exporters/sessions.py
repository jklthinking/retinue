"""Read runtime-owned JSONL conversations into privacy-scoped snapshots.

Inputs are always read-only. The default ``metadata`` mode never copies prompt
or response text. ``summary`` copies short redacted excerpts and ``full`` adds
the most recent redacted messages.
"""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
from typing import Any

from core.protocol.task import ID_RE, ProtocolError


PRIVACY_LEVELS = ("metadata", "summary", "full")
RUNTIME_LABELS = {
    "claude-code": "Claude Code",
    "codex": "Codex",
    "kimi": "Kimi",
    "kimi-legacy": "Kimi",
    "hermes": "Hermes",
}
MAX_TEXT = 4000

_BEARER_RE = re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+/=-]{16,}")
_KEY_RE = re.compile(
    r"\b(?:AKIA[0-9A-Z]{16}|sk-[A-Za-z0-9_-]{20,}|gh[pousr]_[A-Za-z0-9]{20,})\b"
)
_ASSIGN_RE = re.compile(
    r"(?i)\b(api[_-]?key|access[_-]?token|refresh[_-]?token|token|secret|password)"
    r"(\s*[:=]\s*)([^\s,;]{6,})"
)
_PEM_RE = re.compile(
    r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----",
    re.DOTALL,
)
_HOME_PATH_RE = re.compile(
    r"(?:(?:[A-Za-z]:\\Users\\[^\\\s]+)|(?:/(?:root|home/[^/\s]+)))(?:[/\\][^\s,;，。]*)?"
)


def _timestamp(value: Any) -> datetime | None:
    if isinstance(value, (int, float)):
        raw = float(value)
        if raw > 10_000_000_000:
            raw /= 1000
        return datetime.fromtimestamp(raw, timezone.utc)
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def redact_text(value: str) -> str:
    """Strip credentials, keys, and home paths from free text.

    Used by exporters and by the server sync path so a client that skips
    local redaction cannot land secrets in the board database.
    """
    text = _PEM_RE.sub("[private-key-redacted]", value)
    text = _BEARER_RE.sub("Bearer [redacted]", text)
    text = _KEY_RE.sub("[credential-redacted]", text)
    text = _ASSIGN_RE.sub(r"\1\2[redacted]", text)
    text = _HOME_PATH_RE.sub("[local-path]", text)
    return text.strip()[:MAX_TEXT]


def _redact(value: str) -> str:
    return redact_text(value)


def _content_text(value: Any) -> str:
    if isinstance(value, str):
        return _redact(value)
    if isinstance(value, list):
        parts: list[str] = []
        for item in value:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, dict) and item.get("type") in {
                "text",
                "input_text",
                "output_text",
            }:
                if isinstance(item.get("text"), str):
                    parts.append(item["text"])
        return _redact("\n".join(parts))
    if isinstance(value, dict) and isinstance(value.get("text"), str):
        return _redact(value["text"])
    return ""


def _message(record: dict[str, Any], runtime: str) -> tuple[str, str] | None:
    if runtime == "claude-code" and record.get("type") in {"user", "assistant"}:
        message = record.get("message")
        if not isinstance(message, dict):
            return None
        role = message.get("role") or record.get("type")
        text = _content_text(message.get("content"))
        if role in {"user", "assistant"} and text:
            return role, text

    payload = record.get("payload")
    if runtime == "codex" and isinstance(payload, dict):
        if record.get("type") == "response_item" and payload.get("type") == "message":
            role = payload.get("role")
            text = _content_text(payload.get("content"))
            if role in {"user", "assistant"} and text:
                return role, text
        if record.get("type") == "event_msg":
            event_type = payload.get("type")
            role = {"user_message": "user", "agent_message": "assistant"}.get(event_type)
            text = _content_text(payload.get("message"))
            if role and text:
                return role, text

    if runtime == "kimi-legacy":
        role = record.get("role")
        text = _content_text(record.get("content"))
        if role in {"user", "assistant"} and text:
            return role, text

    if runtime == "kimi" and record.get("type") == "context.append_message":
        message = record.get("message")
        if isinstance(message, dict):
            role = message.get("role")
            text = _content_text(message.get("content"))
            if role in {"user", "assistant"} and text:
                return role, text

    if runtime == "hermes":
        role = record.get("role")
        text = _content_text(record.get("content"))
        if role in {"user", "assistant"} and text:
            return role, text
    return None


def _session_id(record: dict[str, Any], runtime: str) -> str | None:
    if runtime == "claude-code":
        value = record.get("sessionId") or record.get("session_id")
        return value if isinstance(value, str) and value else None
    payload = record.get("payload")
    if (
        runtime == "codex"
        and record.get("type") == "session_meta"
        and isinstance(payload, dict)
        and isinstance(payload.get("id"), str)
        and payload["id"]
    ):
        return payload["id"]
    if runtime.startswith("kimi"):
        value = record.get("sessionId") or record.get("session_id")
        return value if isinstance(value, str) and value else None
    return None


_KIMI_SESSION_RE = re.compile(
    r"^(?:ses_)?([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}|continue)$",
    re.IGNORECASE,
)


def _path_session_id(path: Path, runtime: str) -> str | None:
    if runtime.startswith("kimi"):
        for part in reversed(path.parts):
            match = _KIMI_SESSION_RE.fullmatch(part)
            if match:
                return match.group(1).lower()
    if runtime == "hermes":
        return path.stem
    return None


def _kimi_state(path: Path) -> tuple[str, datetime | None, datetime | None]:
    for parent in list(path.parents)[:4]:
        state_path = parent / "state.json"
        try:
            state = json.loads(state_path.read_text(encoding="utf-8", errors="replace"))
        except (OSError, json.JSONDecodeError):
            continue
        title = state.get("title") if isinstance(state.get("title"), str) else ""
        return (
            title.strip(),
            _timestamp(state.get("createdAt")),
            _timestamp(state.get("updatedAt")),
        )
    return "", None, None


def _one_line(value: str, limit: int) -> str:
    compact = " ".join(value.split())
    return compact if len(compact) <= limit else compact[: limit - 1].rstrip() + "…"


def _fallback_id(path: Path, source: Path) -> str:
    relative = path.relative_to(source).as_posix().encode("utf-8")
    return "file-" + hashlib.sha256(relative).hexdigest()[:24]


def _parse_file(
    path: Path,
    source: Path,
    runtime: str,
    privacy: str,
    max_messages: int,
) -> dict[str, Any] | None:
    external_id = _path_session_id(path, runtime)
    started: datetime | None = None
    updated: datetime | None = None
    cursor = 0
    messages: list[dict[str, Any]] = []

    try:
        with path.open(encoding="utf-8", errors="replace") as stream:
            for cursor, line in enumerate(stream, 1):
                try:
                    record = json.loads(line)
                except (json.JSONDecodeError, UnicodeDecodeError):
                    continue
                if not isinstance(record, dict):
                    continue
                external_id = external_id or _session_id(record, runtime)
                at = _timestamp(record.get("timestamp"))
                if at is not None:
                    started = at if started is None or at < started else started
                    updated = at if updated is None or at > updated else updated
                item = _message(record, runtime)
                if item is None:
                    continue
                role, text = item
                candidate = {
                    "role": role,
                    "text": text,
                    "at": at.isoformat() if at else None,
                }
                if messages and messages[-1]["role"] == role and messages[-1]["text"] == text:
                    continue
                messages.append(candidate)
    except (OSError, UnicodeDecodeError):
        return None

    if cursor == 0:
        return None
    external_id = external_id or _fallback_id(path, source)
    state_title = ""
    if runtime.startswith("kimi"):
        state_title, state_started, state_updated = _kimi_state(path)
        if state_started is not None:
            started = state_started if started is None or state_started < started else started
        if state_updated is not None:
            updated = state_updated if updated is None or state_updated > updated else updated
    if updated is None:
        try:
            file_updated = datetime.fromtimestamp(path.stat().st_mtime, timezone.utc)
        except OSError:
            file_updated = None
        if file_updated is not None:
            started = started or file_updated
            updated = file_updated
    label = RUNTIME_LABELS[runtime]
    dated = updated.astimezone().strftime("%m月%d日 %H:%M") if updated else "本地会话"
    title = state_title or f"{label} 会话 · {dated}"
    summary = ""
    visible_messages: list[dict[str, Any]] = []

    if privacy != "metadata":
        first_user = next((m["text"] for m in messages if m["role"] == "user"), "")
        last_user = next((m["text"] for m in reversed(messages) if m["role"] == "user"), "")
        last_assistant = next(
            (m["text"] for m in reversed(messages) if m["role"] == "assistant"), ""
        )
        if first_user and not state_title:
            title = _one_line(first_user, 96)
        summary_parts = []
        if last_user:
            summary_parts.append("最近请求：" + _one_line(last_user, 240))
        if last_assistant:
            summary_parts.append("最近回复：" + _one_line(last_assistant, 360))
        summary = "\n".join(summary_parts)
        if privacy == "full":
            visible_messages = messages[-max_messages:]

    return {
        "runtime": runtime,
        "external_id": external_id,
        "title": title,
        "summary": summary,
        "privacy": privacy,
        "cursor": cursor,
        "message_count": len(messages),
        "messages": visible_messages,
        "started_at": started.isoformat() if started else None,
        "updated_at": updated.isoformat() if updated else None,
        "task_id": None,
        "resume_capable": False,
    }


def collect_sessions(
    source: Path | str,
    *,
    runtime: str,
    agent_id: str,
    privacy: str = "metadata",
    limit: int = 50,
    max_messages: int = 40,
) -> list[dict[str, Any]]:
    """Return newest runtime sessions without changing the source directory."""
    source = Path(source).expanduser().resolve()
    if not source.is_dir():
        raise ProtocolError(f"runtime session directory does not exist: {source}")
    if runtime not in RUNTIME_LABELS:
        raise ProtocolError(f"unsupported runtime: {runtime}")
    if not ID_RE.fullmatch(agent_id):
        raise ProtocolError("agent id must use lowercase letters, digits, and hyphens")
    if privacy not in PRIVACY_LEVELS:
        raise ProtocolError(f"privacy must be one of: {', '.join(PRIVACY_LEVELS)}")
    if not 1 <= limit <= 500:
        raise ProtocolError("limit must be between 1 and 500")
    if not 1 <= max_messages <= 80:
        raise ProtocolError("max_messages must be between 1 and 80")

    if runtime == "claude-code":
        candidates = [
            item for item in source.rglob("*.jsonl")
            if "subagents" not in item.parts and "tasks" not in item.parts
        ]
    elif runtime == "kimi-legacy":
        candidates = [
            item for item in source.rglob("context.jsonl")
            if "subagents" not in item.parts and "tasks" not in item.parts
        ]
    elif runtime == "kimi":
        candidates = [
            item for item in source.rglob("wire.jsonl")
            if "subagents" not in item.parts and "tasks" not in item.parts
        ]
    elif runtime == "hermes":
        candidates = list(source.glob("*.jsonl"))
    else:
        candidates = list(source.rglob("*.jsonl"))
    paths = sorted(
        candidates,
        key=lambda item: item.stat().st_mtime if item.is_file() else 0,
        reverse=True,
    )
    snapshots = []
    for path in paths:
        snapshot = _parse_file(path, source, runtime, privacy, max_messages)
        if snapshot is not None:
            snapshot["actor_id"] = agent_id
            snapshots.append(snapshot)
        if len(snapshots) >= limit:
            break
    snapshots.sort(key=lambda item: item.get("updated_at") or "", reverse=True)
    return snapshots
