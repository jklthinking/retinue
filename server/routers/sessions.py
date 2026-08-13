"""Runtime session routes: sync, listing, captures, and session-born tasks."""

from __future__ import annotations

import datetime as dt
import hashlib
import json
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from adapters.exporters.sessions import redact_text
from core.protocol.task import ProtocolError

from ..db import Actor, RuntimeSession, SessionCapture, Task, utcnow
from ..deps import Principal, get_db, require_auth, wrap_protocol_errors
from ..engine import create_task
from ..helpers import notify_feishu, task_response
from ..schemas import (
    SessionCaptureBody,
    SessionCaptureExportBody,
    SessionSyncBody,
    SessionTaskBody,
)

router = APIRouter()


def session_utc(value: dt.datetime | None) -> dt.datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=dt.timezone.utc)
    return value.astimezone(dt.timezone.utc)


def session_time(value: dt.datetime | None) -> str | None:
    normalized = session_utc(value)
    return normalized.isoformat().replace("+00:00", "Z") if normalized else None


def runtime_session_to_dict(
    row: RuntimeSession,
    db: Session,
    *,
    include_messages: bool = False,
) -> dict[str, Any]:
    actor = db.get(Actor, row.actor_id)
    task = db.get(Task, row.task_id) if row.task_id else None
    return {
        "id": row.id,
        "actor_id": row.actor_id,
        "actor_name": (
            actor.display_name if actor and actor.display_name else row.actor_id
        ),
        "runtime": row.runtime,
        "node": row.node,
        "title": row.title,
        "summary": row.summary,
        "privacy": row.privacy,
        "cursor": row.cursor,
        "message_count": row.message_count,
        "messages": json.loads(row.messages_json) if include_messages else [],
        "task_id": row.task_id,
        "task_title": task.title if task else None,
        "resume_capable": row.resume_capable,
        "started_at": session_time(row.started_at),
        "updated_at": session_time(row.updated_at),
        "synced_at": session_time(row.synced_at),
    }


def session_body_hash(body: SessionSyncBody, actor_id: str) -> str:
    payload = body.model_dump(mode="json", exclude={"actor_id"})
    payload["actor_id"] = actor_id
    raw = json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def require_session_access(row: RuntimeSession, principal: Principal) -> None:
    if principal.kind == "agent" and row.actor_id != principal.actor_id:
        raise HTTPException(status_code=403, detail="session belongs to another actor")


def capture_markdown(
    row: RuntimeSession, db: Session, requested_title: str = ""
) -> tuple[str, str]:
    actor = db.get(Actor, row.actor_id)
    actor_name = actor.display_name if actor and actor.display_name else row.actor_id
    title = requested_title.strip() or f"{actor_name} · {row.runtime} 会话"
    updated = session_time(row.updated_at) or session_time(row.synced_at) or ""
    task_line = f"related_task: {row.task_id}" if row.task_id else "related_task: "
    summary = row.summary.strip()
    source_note = (
        summary
        if summary
        else "当前仅同步会话元数据；如需提炼语义，请在原设备将该会话主动升级为“脱敏摘要”后再次同步。"
    )
    markdown = "\n".join(
        [
            "---",
            "type: conversation_capture",
            "source: retinue",
            f"session_id: {row.id}",
            f"actor: {row.actor_id}",
            f"runtime: {row.runtime}",
            f"privacy: {row.privacy}",
            f"message_count: {row.message_count}",
            f"updated_at: {updated}",
            task_line,
            "tags: [inbox, conversation, retinue]",
            "---",
            "",
            f"# {title}",
            "",
            "## 来源",
            f"- 众卿会话索引：session:{row.id}",
            f"- 执行者：{actor_name}（{row.runtime}）",
            f"- 同步范围：{row.privacy}",
            "",
            "## 已同步摘要",
            source_note,
            "",
            "## 待确认的提取",
            "- [ ] 是否沉淀为知识卡或 Skill？",
            "- [ ] 是否需要转为可验收的任务卡？",
            "- [ ] 如已形成任务，补充交付物引用和验收结果。",
            "",
            "## 关联",
            f"- Retinue Session: {row.id}",
            *( [f"- Task: {row.task_id}"] if row.task_id else [] ),
            "",
        ]
    )
    return title, markdown


def recap_markdown(row: RuntimeSession, db: Session) -> tuple[str, str]:
    actor = db.get(Actor, row.actor_id)
    actor_name = actor.display_name if actor and actor.display_name else row.actor_id
    updated = row.updated_at or row.synced_at
    if updated.tzinfo is None:
        updated = updated.replace(tzinfo=dt.timezone.utc)
    local_updated = updated.astimezone()
    date_text = local_updated.strftime("%Y-%m-%d")
    topic = row.title.strip() or f"{actor_name} {row.runtime} 会话"
    task_line = f'"{row.task_id}"' if row.task_id else '""'
    summary = row.summary.strip() or "本次会话没有可归档的脱敏摘要。"
    markdown = "\n".join(
        [
            "---",
            "type: topic-archive",
            f"date: {date_text}",
            f"bot: {json.dumps(actor_name, ensure_ascii=False)}",
            f"platform: {json.dumps(row.runtime, ensure_ascii=False)}",
            'chat_id: ""',
            'thread_id: ""',
            f"session_id: {json.dumps('retinue-' + str(row.id), ensure_ascii=False)}",
            f"topic: {json.dumps(topic, ensure_ascii=False)}",
            'status: "archived"',
            f"related_task: {task_line}",
            f"source_node: {json.dumps(row.node, ensure_ascii=False)}",
            f"source_ref: {json.dumps('retinue-session:' + str(row.id), ensure_ascii=False)}",
            "generated: true",
            f"tags: [话题归档, retinue, {row.runtime}]",
            "---",
            "",
            f"# {topic}",
            "",
            "## Recap",
            summary,
            "",
            "## 来源证据",
            f"- 众卿会话：session:{row.id}",
            f"- 来源节点：{row.node or 'unknown'}",
            f"- 运行时：{row.runtime}",
            f"- 消息数：{row.message_count}",
            f"- 最后活动：{session_time(row.updated_at) or ''}",
            *([f"- 关联任务：{row.task_id}"] if row.task_id else []),
            "",
            "## 后续动作",
            "- [ ] 是否需要转为任务卡？",
            "- [ ] 是否沉淀到长期知识或 Skill？",
            "",
        ]
    )
    return topic, markdown


def maybe_queue_session_recap(
    row: RuntimeSession, db: Session, requested_by: str
) -> SessionCapture | None:
    if (
        row.privacy not in {"summary", "full"}
        or not row.summary.strip()
        or row.updated_at is None
    ):
        return None
    updated = row.updated_at
    if updated.tzinfo is None:
        updated = updated.replace(tzinfo=dt.timezone.utc)
    if utcnow() - updated < dt.timedelta(minutes=45):
        return None
    title, markdown = recap_markdown(row, db)
    capture = db.execute(
        select(SessionCapture)
        .where(SessionCapture.session_id == row.id)
        .where(SessionCapture.kind == "recap")
    ).scalar_one_or_none()
    if capture is None:
        capture = SessionCapture(
            session_id=row.id,
            actor_id=row.actor_id,
            kind="recap",
            requested_by=requested_by,
        )
        db.add(capture)
    changed = capture.markdown != markdown
    capture.title = title
    capture.markdown = markdown
    if changed or capture.status != "exported":
        capture.status = "queued"
        capture.target_path = ""
        capture.exported_at = None
    return capture


def capture_to_dict(capture: SessionCapture) -> dict[str, Any]:
    return {
        "id": capture.id,
        "session_id": capture.session_id,
        "actor_id": capture.actor_id,
        "kind": capture.kind,
        "title": capture.title,
        "markdown": capture.markdown,
        "status": capture.status,
        "target_path": capture.target_path,
        "created_at": session_time(capture.created_at),
        "exported_at": session_time(capture.exported_at),
    }


@router.post("/api/sessions/sync")
def sync_runtime_session(
    body: SessionSyncBody,
    principal: Principal = Depends(require_auth),
    db: Session = Depends(get_db, scope="function"),
) -> dict[str, Any]:
    if principal.kind == "agent":
        if body.actor_id and body.actor_id != principal.actor_id:
            raise HTTPException(
                status_code=403, detail="agents may only sync their own sessions"
            )
        actor_id = principal.actor_id
    else:
        actor_id = body.actor_id or principal.actor_id
    if not actor_id:
        raise HTTPException(status_code=422, detail="actor_id is required")
    actor = db.get(Actor, actor_id)
    if actor is None:
        raise HTTPException(status_code=422, detail=f"unknown actor: {actor_id}")
    if body.task_id and db.get(Task, body.task_id) is None:
        raise HTTPException(status_code=422, detail=f"unknown task: {body.task_id}")
    if body.message_count < len(body.messages):
        raise HTTPException(
            status_code=422,
            detail="message_count cannot be smaller than synced messages",
        )
    if body.privacy == "metadata" and (body.summary or body.messages):
        raise HTTPException(
            status_code=422,
            detail="metadata privacy cannot include summary or messages",
        )
    if body.privacy == "summary" and body.messages:
        raise HTTPException(
            status_code=422, detail="summary privacy cannot include messages"
        )

    # Server-side guard: never trust the exporter alone with secrets.
    body.title = redact_text(body.title)
    body.summary = redact_text(body.summary)
    for message in body.messages:
        message.text = redact_text(message.text)

    digest = session_body_hash(body, actor_id)
    row = db.execute(
        select(RuntimeSession)
        .where(RuntimeSession.actor_id == actor_id)
        .where(RuntimeSession.runtime == body.runtime)
        .where(RuntimeSession.external_id == body.external_id)
    ).scalar()
    status = "created"
    if row is not None:
        if body.cursor < row.cursor:
            raise HTTPException(status_code=409, detail="stale session cursor")
        if (
            body.cursor == row.cursor
            and digest != row.content_hash
            and body.privacy == row.privacy
        ):
            raise HTTPException(status_code=409, detail="session cursor conflict")
        if body.cursor == row.cursor and digest == row.content_hash:
            row.synced_at = utcnow()
            db.flush()
            maybe_queue_session_recap(row, db, f"auto:{actor_id}")
            result = runtime_session_to_dict(row, db, include_messages=True)
            result["sync_status"] = "unchanged"
            return result
        status = "updated"
    else:
        row = RuntimeSession(
            actor_id=actor_id,
            runtime=body.runtime,
            external_id=body.external_id,
            content_hash=digest,
        )
        db.add(row)

    row.node = actor.node
    row.title = body.title.strip()
    row.summary = body.summary.strip()
    row.privacy = body.privacy
    row.cursor = body.cursor
    row.content_hash = digest
    row.message_count = body.message_count
    row.messages_json = json.dumps(
        [message.model_dump(mode="json") for message in body.messages],
        ensure_ascii=False,
    )
    row.task_id = body.task_id
    row.resume_capable = body.resume_capable
    row.started_at = session_utc(body.started_at)
    row.updated_at = session_utc(body.updated_at)
    row.synced_at = utcnow()
    db.flush()
    maybe_queue_session_recap(row, db, f"auto:{actor_id}")
    result = runtime_session_to_dict(row, db, include_messages=True)
    result["sync_status"] = status
    return result


@router.get("/api/sessions")
def list_runtime_sessions(
    q: str = "",
    actor_id: str | None = None,
    runtime: str | None = None,
    privacy: str | None = None,
    task_id: str | None = None,
    limit: int = 100,
    principal: Principal = Depends(require_auth),
    db: Session = Depends(get_db, scope="function"),
) -> list[dict[str, Any]]:
    if len(q) > 200:
        raise HTTPException(status_code=422, detail="搜索内容不能超过 200 字")
    query = select(RuntimeSession)
    if principal.kind == "agent":
        query = query.where(RuntimeSession.actor_id == principal.actor_id)
    elif actor_id:
        query = query.where(RuntimeSession.actor_id == actor_id)
    if task_id:
        query = query.where(RuntimeSession.task_id == task_id)
    if runtime:
        query = query.where(RuntimeSession.runtime == runtime)
    if privacy:
        if privacy not in {"metadata", "summary", "full"}:
            raise HTTPException(status_code=422, detail="unknown privacy level")
        query = query.where(RuntimeSession.privacy == privacy)
    if q.strip():
        term = f"%{q.strip()}%"
        query = query.where(
            or_(RuntimeSession.title.like(term), RuntimeSession.summary.like(term))
        )
    rows = db.execute(
        query.order_by(
            RuntimeSession.updated_at.desc(),
            RuntimeSession.synced_at.desc(),
            RuntimeSession.id.desc(),
        ).limit(max(1, min(limit, 200)))
    ).scalars()
    return [runtime_session_to_dict(row, db) for row in rows]


@router.get("/api/sessions/{session_id}")
def get_runtime_session(
    session_id: int,
    principal: Principal = Depends(require_auth),
    db: Session = Depends(get_db, scope="function"),
) -> dict[str, Any]:
    row = db.get(RuntimeSession, session_id)
    if row is None:
        raise HTTPException(status_code=404, detail="session not found")
    if principal.kind == "agent" and row.actor_id != principal.actor_id:
        raise HTTPException(status_code=403, detail="session belongs to another actor")
    return runtime_session_to_dict(row, db, include_messages=True)


@router.get("/api/sessions/{session_id}/captures")
def get_session_captures(
    session_id: int,
    principal: Principal = Depends(require_auth),
    db: Session = Depends(get_db, scope="function"),
) -> list[dict[str, Any]]:
    row = db.get(RuntimeSession, session_id)
    if row is None:
        raise HTTPException(status_code=404, detail="session not found")
    require_session_access(row, principal)
    captures = db.execute(
        select(SessionCapture)
        .where(SessionCapture.session_id == row.id)
        .order_by(SessionCapture.id.desc())
    ).scalars()
    return [capture_to_dict(item) for item in captures]


@router.post("/api/sessions/{session_id}/capture-obsidian")
def queue_obsidian_capture(
    session_id: int,
    body: SessionCaptureBody,
    principal: Principal = Depends(require_auth),
    db: Session = Depends(get_db, scope="function"),
) -> dict[str, Any]:
    row = db.get(RuntimeSession, session_id)
    if row is None:
        raise HTTPException(status_code=404, detail="session not found")
    require_session_access(row, principal)
    capture = db.execute(
        select(SessionCapture)
        .where(SessionCapture.session_id == row.id)
        .where(SessionCapture.kind == "obsidian")
    ).scalar_one_or_none()
    title, markdown = capture_markdown(row, db, body.title)
    if capture is None:
        capture = SessionCapture(
            session_id=row.id,
            actor_id=row.actor_id,
            kind="obsidian",
            requested_by=principal.write_identity,
        )
        db.add(capture)
    capture.title = title
    capture.markdown = markdown
    capture.status = "queued"
    capture.target_path = ""
    capture.exported_at = None
    db.flush()
    return capture_to_dict(capture)


@router.get("/api/session-captures/pending")
def get_pending_session_captures(
    principal: Principal = Depends(require_auth),
    db: Session = Depends(get_db, scope="function"),
) -> list[dict[str, Any]]:
    query = select(SessionCapture).where(SessionCapture.status == "queued")
    if principal.kind == "agent":
        query = query.where(SessionCapture.actor_id == principal.actor_id)
    rows = db.execute(query.order_by(SessionCapture.id.asc()).limit(100)).scalars()
    return [capture_to_dict(row) for row in rows]


@router.post("/api/session-captures/{capture_id}/exported")
def mark_session_capture_exported(
    capture_id: int,
    body: SessionCaptureExportBody,
    principal: Principal = Depends(require_auth),
    db: Session = Depends(get_db, scope="function"),
) -> dict[str, Any]:
    capture = db.get(SessionCapture, capture_id)
    if capture is None:
        raise HTTPException(status_code=404, detail="session capture not found")
    if principal.kind == "agent" and capture.actor_id != principal.actor_id:
        raise HTTPException(status_code=403, detail="capture belongs to another actor")
    capture.status = "exported"
    capture.target_path = body.target_path.strip()
    capture.exported_at = utcnow()
    db.flush()
    return capture_to_dict(capture)


@router.post("/api/sessions/{session_id}/create-task")
def create_task_from_session(
    session_id: int,
    body: SessionTaskBody,
    principal: Principal = Depends(require_auth),
    db: Session = Depends(get_db, scope="function"),
) -> dict[str, Any]:
    row = db.get(RuntimeSession, session_id)
    if row is None:
        raise HTTPException(status_code=404, detail="session not found")
    require_session_access(row, principal)
    if row.task_id:
        raise HTTPException(status_code=409, detail=f"session already links to task {row.task_id}")
    creator = principal.write_identity
    if db.get(Actor, creator) is None:
        raise HTTPException(status_code=422, detail=f"current account has no actor: {creator!r}")
    holder = body.holder or creator
    if principal.kind == "agent" and holder != principal.actor_id:
        raise HTTPException(status_code=403, detail="agents may only create tasks for themselves")
    try:
        task = create_task(
            db,
            title=body.title,
            created_by=creator,
            holder=holder,
            dept=body.dept,
            priority=body.priority,
            acceptance=body.acceptance,
            refs=[f"session:{row.id}"],
            note=f"从会话索引 session:{row.id} 创建任务",
        )
    except ProtocolError as exc:
        raise wrap_protocol_errors(exc) from exc
    row.task_id = task.id
    db.flush()
    result = task_response(task)
    result["session_id"] = row.id
    notify_feishu(result["receipt"])
    return result
