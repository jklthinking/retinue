"""Intake command grammar M1: in-chat progress, note, status, and done.

Channel adapters stay dumb (text / sender_id / message_id). The hub parses the
first line of ``body.text`` and either opens a hall card (M0 default) or mutates
an existing card signed by the mapped board user. Write intents reuse the
``intake:{channel}:{message_id}`` event_key so platform retries are idempotent.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Literal

from sqlalchemy import select
from sqlalchemy.orm import Session

from .db import Task, TaskEvent
from .engine import Forbidden, ProtocolError, update_task
from .helpers import task_response
from .intake import (
    TITLE_LIMIT,
    UnmappedChannelUser,
    message_digest,
    open_channel_card,
    resolve_channel_actor,
)

Intent = Literal["open", "progress", "note", "status", "done"]

TASK_ID_TOKEN = r"task-[0-9]{8}-[0-9]{3}"

_PROGRESS_RE = re.compile(
    rf"^(?:进度|progress)\s+({TASK_ID_TOKEN})\s+(\d{{1,3}})(?:\s+(.*))?$",
    re.IGNORECASE | re.DOTALL,
)
_NOTE_RE = re.compile(
    rf"^(?:备注|note)\s+({TASK_ID_TOKEN})\s+(.+)$",
    re.IGNORECASE | re.DOTALL,
)
_STATUS_RE = re.compile(
    rf"^(?:查|状态|status)\s+({TASK_ID_TOKEN})\s*$",
    re.IGNORECASE,
)
_DONE_RE = re.compile(
    rf"^(?:完成|done)\s+({TASK_ID_TOKEN})(?:\s+(.*))?$",
    re.IGNORECASE | re.DOTALL,
)
_OPEN_PREFIX_RE = re.compile(r"^(?:开卡|new)\s+(.+)$", re.IGNORECASE | re.DOTALL)


@dataclass(frozen=True)
class ParsedCommand:
    intent: Intent
    task_id: str | None = None
    progress: int | None = None
    note: str | None = None
    title: str | None = None
    error: str | None = None


def parse_intake_command(text: str) -> ParsedCommand:
    """Parse the first line of an inbound message into one intake intent."""
    lines = text.strip().splitlines()
    first = (lines[0] if lines else "").strip()
    rest = "\n".join(lines[1:]).strip()

    m = _PROGRESS_RE.match(first)
    if m:
        value = int(m.group(2))
        if value > 100:
            return ParsedCommand(
                intent="progress",
                task_id=m.group(1),
                error="进度必须是 0 到 100 的整数",
            )
        note = (m.group(3) or "").strip()
        if rest:
            note = f"{note}\n{rest}".strip() if note else rest
        return ParsedCommand(
            intent="progress",
            task_id=m.group(1),
            progress=value,
            note=note or None,
        )

    if re.match(r"^(?:进度|progress)\b", first, re.IGNORECASE):
        return ParsedCommand(
            intent="progress",
            error="进度指令格式：进度 task-YYYYMMDD-NNN 数字 [说明]",
        )

    m = _NOTE_RE.match(first)
    if m:
        note = m.group(2).strip()
        if rest:
            note = f"{note}\n{rest}".strip()
        return ParsedCommand(intent="note", task_id=m.group(1), note=note)

    if re.match(r"^(?:备注|note)\b", first, re.IGNORECASE):
        return ParsedCommand(
            intent="note",
            error="备注指令格式：备注 task-YYYYMMDD-NNN 文字",
        )

    m = _STATUS_RE.match(first)
    if m:
        return ParsedCommand(intent="status", task_id=m.group(1))

    if re.match(r"^(?:查|状态|status)\b", first, re.IGNORECASE):
        return ParsedCommand(
            intent="status",
            error="查询指令格式：查 task-YYYYMMDD-NNN",
        )

    m = _DONE_RE.match(first)
    if m:
        note = (m.group(2) or "").strip()
        if rest:
            note = f"{note}\n{rest}".strip() if note else rest
        return ParsedCommand(intent="done", task_id=m.group(1), note=note or None)

    if re.match(r"^(?:完成|done)\b", first, re.IGNORECASE):
        return ParsedCommand(
            intent="done",
            error="完成指令格式：完成 task-YYYYMMDD-NNN [说明]",
        )

    m = _OPEN_PREFIX_RE.match(first)
    if m:
        title = m.group(1).strip().splitlines()[0][:TITLE_LIMIT]
        if not title:
            return ParsedCommand(intent="open", error="开卡需要标题")
        return ParsedCommand(intent="open", title=title)

    title = first[:TITLE_LIMIT] if first else text.strip()[:TITLE_LIMIT]
    return ParsedCommand(intent="open", title=title)


def _event_by_key(db: Session, event_key: str) -> TaskEvent | None:
    return db.execute(
        select(TaskEvent).where(TaskEvent.event_key == event_key)
    ).scalar()


def _load_task(db: Session, task_id: str) -> Task | None:
    return db.get(Task, task_id)


def _stamp_event_key(db: Session, task: Task, event_key: str) -> None:
    """Attach the intake idempotency key to the newest chain event."""
    if not task.events:
        db.refresh(task)
    newest = max(task.events, key=lambda event: event.seq)
    if newest.event_key is None:
        newest.event_key = event_key
        db.flush()


def _open_reply(task_id: str) -> str:
    return f"已开卡 {task_id}，等待执行者接单。"


def _progress_reply(
    task_id: str, progress: int, note: str | None, *, as_note: bool
) -> str:
    suffix = f"：{note}" if note else ""
    if as_note:
        return (
            f"卡 {task_id} 当前不在进行中，已将进度 {progress}% 记为备注{suffix}"
        )
    return f"已记录进度 {progress}%：{task_id}{suffix}"


def _note_reply(task_id: str, note: str) -> str:
    brief = note if len(note) <= 80 else note[:77] + "…"
    return f"已追加备注：{task_id} {brief}"


def _status_reply(task: Task) -> str:
    reason = f"，阻塞原因：{task.blocked_reason}" if task.blocked_reason else ""
    return (
        f"任务 {task.id} 状态 {task.status}，持棒 {task.holder}，"
        f"进度 {int(task.progress or 0)}%{reason}"
    )


def _done_reply(task_id: str, note: str | None) -> str:
    suffix = f"：{note}" if note else ""
    return f"已将 {task_id} 标为完成{suffix}"


def _done_refused_reply(task: Task, who: str) -> str:
    return (
        f"无法将 {task.id} 标为完成：当前持棒人是 {task.holder}，"
        f"你（{who}）不是持棒人。"
    )


def _response_open(task: Task) -> dict[str, Any]:
    result = task_response(task)
    return {
        "task_id": task.id,
        "status": task.status,
        "created_by": task.created_by,
        "receipt": result["receipt"],
        "intent": "open",
        "reply": _open_reply(task.id),
    }


def _response_progress(
    task: Task, progress: int, note: str | None, *, as_note: bool
) -> dict[str, Any]:
    return {
        "task_id": task.id,
        "status": task.status,
        "progress": int(task.progress or 0),
        "intent": "progress",
        "reply": _progress_reply(task.id, progress, note, as_note=as_note),
    }


def _response_note(task: Task, note: str) -> dict[str, Any]:
    return {
        "task_id": task.id,
        "status": task.status,
        "intent": "note",
        "reply": _note_reply(task.id, note),
    }


def _response_status(task: Task) -> dict[str, Any]:
    return {
        "task_id": task.id,
        "status": task.status,
        "holder": task.holder,
        "progress": int(task.progress or 0),
        "intent": "status",
        "reply": _status_reply(task),
    }


def _response_done(
    task: Task, note: str | None, *, refused: bool = False, who: str = ""
) -> dict[str, Any]:
    if refused:
        return {
            "task_id": task.id,
            "status": task.status,
            "holder": task.holder,
            "intent": "done",
            "refused": True,
            "reply": _done_refused_reply(task, who),
        }
    return {
        "task_id": task.id,
        "status": task.status,
        "progress": int(task.progress or 0),
        "intent": "done",
        "reply": _done_reply(task.id, note),
    }


def _error_response(
    intent: Intent, message: str, task_id: str | None = None
) -> dict[str, Any]:
    body: dict[str, Any] = {"intent": intent, "reply": message, "error": True}
    if task_id:
        body["task_id"] = task_id
    return body


def _replay_write_response(
    db: Session, event: TaskEvent, parsed: ParsedCommand
) -> dict[str, Any]:
    task = _load_task(db, event.task_id)
    if task is None:
        return _error_response(
            parsed.intent, f"未找到任务 {event.task_id}", event.task_id
        )
    if parsed.intent == "progress":
        progress = (
            parsed.progress
            if parsed.progress is not None
            else int(task.progress or 0)
        )
        as_note = "记为备注" in (event.did or "") or (
            "不在进行中" in (event.did or "")
        )
        return _response_progress(task, progress, parsed.note, as_note=as_note)
    if parsed.intent == "note":
        return _response_note(task, parsed.note or event.did or "")
    if parsed.intent == "done":
        return _response_done(task, parsed.note)
    return _error_response(parsed.intent, "无法重放该入站事件", task.id)


def handle_intake_message(
    db: Session,
    *,
    channel_id: str,
    channel_user_id: str,
    text: str,
    message_id: str,
) -> dict[str, Any]:
    """Dispatch one inbound channel message to open / progress / note / status / done."""
    actor = resolve_channel_actor(
        db, channel_id=channel_id, channel_user_id=channel_user_id
    )
    parsed = parse_intake_command(text)
    event_key = f"intake:{channel_id}:{message_id}"

    if parsed.error:
        return _error_response(parsed.intent, parsed.error, parsed.task_id)

    if parsed.intent == "open":
        note = (
            f"通道开卡 [{channel_id}] 用户 {channel_user_id}: "
            + message_digest(text, message_id)
        )
        task = open_channel_card(
            db,
            channel_id=channel_id,
            channel_user_id=channel_user_id,
            title=parsed.title or text.strip().splitlines()[0][:TITLE_LIMIT],
            note=note,
            acceptance=[f"原始消息: {message_digest(text, message_id)}"],
            event_key=event_key,
        )
        return _response_open(task)

    assert parsed.task_id is not None
    task = _load_task(db, parsed.task_id)
    if task is None:
        return _error_response(
            parsed.intent, f"未找到任务 {parsed.task_id}", parsed.task_id
        )

    if parsed.intent == "status":
        return _response_status(task)

    existing = _event_by_key(db, event_key)
    if existing is not None:
        return _replay_write_response(db, existing, parsed)

    if parsed.intent == "progress":
        assert parsed.progress is not None
        if task.status in ("done", "cancelled"):
            return _error_response(
                "progress",
                f"任务 {task.id} 已结束（{task.status}），无法再记进度",
                task.id,
            )
        progress_value = parsed.progress
        detail = parsed.note or (
            f"通道进度 [{channel_id}] {message_digest(text, message_id)}"
        )
        as_note = task.status != "doing"
        if as_note:
            note = f"进度 {progress_value}%（卡不在进行中，记为备注）: {detail}"
            update_task(
                db,
                task,
                who=actor.id,
                is_privileged=True,
                note=note,
            )
            _stamp_event_key(db, task, event_key)
            return _response_progress(
                task, progress_value, parsed.note, as_note=True
            )

        note = f"进度 {progress_value}%: {detail}"
        update_task(
            db,
            task,
            who=actor.id,
            is_privileged=True,
            progress=progress_value,
            note=note,
        )
        _stamp_event_key(db, task, event_key)
        return _response_progress(
            task, progress_value, parsed.note, as_note=False
        )

    if parsed.intent == "note":
        assert parsed.note is not None
        if task.status in ("done", "cancelled"):
            return _error_response(
                "note",
                f"任务 {task.id} 已结束（{task.status}），无法再追加备注",
                task.id,
            )
        note = f"通道备注 [{channel_id}]: {parsed.note}"
        update_task(
            db,
            task,
            who=actor.id,
            is_privileged=True,
            note=note,
        )
        _stamp_event_key(db, task, event_key)
        return _response_note(task, parsed.note)

    if parsed.intent == "done":
        if task.status in ("done", "cancelled"):
            return _error_response(
                "done",
                f"任务 {task.id} 已是终态（{task.status}）",
                task.id,
            )
        detail = parsed.note or (
            f"通道完成 [{channel_id}] {message_digest(text, message_id)}"
        )
        try:
            update_task(
                db,
                task,
                who=actor.id,
                is_privileged=False,
                status="done",
                note=detail,
            )
        except Forbidden:
            return _response_done(task, parsed.note, refused=True, who=actor.id)
        except ProtocolError as exc:
            return _error_response("done", f"无法完成 {task.id}：{exc}", task.id)
        _stamp_event_key(db, task, event_key)
        return _response_done(task, parsed.note)

    return _error_response("open", "未知入站意图")


__all__ = [
    "ParsedCommand",
    "UnmappedChannelUser",
    "handle_intake_message",
    "parse_intake_command",
]