"""Session-to-memory distillation candidates (M0).

The board holds entities, a cooling gate, an approval gate, and an audit
chain. Summary/distillation intelligence lives outside the server: executors
register candidates through the API. This module never rewrites a source
session row and never auto-promotes a candidate.
"""

from __future__ import annotations

import datetime as dt
import json
import os
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from core.protocol.task import ProtocolError

from .db import DistillCandidate, DistillEvent, KnowledgeSource, RuntimeSession, utcnow
from .deps import Principal
from .engine import Conflict, Forbidden


STATUS_PENDING = "pending"
STATUS_PROMOTED = "promoted"
STATUS_REJECTED = "rejected"
ACTIVE_STATUSES = (STATUS_PENDING, STATUS_PROMOTED, STATUS_REJECTED)

EVENT_REGISTERED = "registered"
EVENT_PROMOTED = "promoted"
EVENT_REJECTED = "rejected"

SUMMARY_MAX = 2000
ORIGIN_REF_MAX = 512
DEFAULT_COOLDOWN_HOURS = 24
KNOWLEDGE_KIND = "distill"


def cooldown_hours_default() -> int:
    raw = os.environ.get("RETINUE_DISTILL_COOLDOWN_HOURS", "").strip()
    if not raw:
        return DEFAULT_COOLDOWN_HOURS
    try:
        hours = int(raw)
    except ValueError as exc:
        raise ProtocolError(
            "RETINUE_DISTILL_COOLDOWN_HOURS must be a positive integer"
        ) from exc
    if hours < 0:
        raise ProtocolError(
            "RETINUE_DISTILL_COOLDOWN_HOURS must be a non-negative integer"
        )
    return hours


def _iso(value: dt.datetime | None) -> str | None:
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=dt.timezone.utc)
    else:
        value = value.astimezone(dt.timezone.utc)
    return value.isoformat().replace("+00:00", "Z")


def _assert_not_viewer(principal: Principal) -> None:
    if principal.role == "viewer":
        raise Forbidden("viewers cannot use distillation routes")


def _assert_privileged(principal: Principal) -> None:
    """Approval gate for M0: promote/reject require a privileged human role.

    The board's Approval rows are task-pipeline queen gates (task_id +
    stage_index). Distill candidates are not pipeline stages, so M0 reuses the
    privilege check rather than inventing a parallel approval entity.
    """
    _assert_not_viewer(principal)
    if not principal.privileged:
        raise Forbidden("privileged session required to decide distillation")


def _next_event_seq(db: Session, candidate_id: int) -> int:
    current = db.execute(
        select(func.max(DistillEvent.seq)).where(
            DistillEvent.candidate_id == candidate_id
        )
    ).scalar()
    return int(current or 0) + 1


def _append_event(
    db: Session,
    candidate: DistillCandidate,
    *,
    event_type: str,
    principal: Principal,
    did: str,
    reason: str | None = None,
    payload: dict[str, Any] | None = None,
) -> DistillEvent:
    event = DistillEvent(
        candidate_id=candidate.id,
        seq=_next_event_seq(db, candidate.id),
        event_type=event_type,
        who=principal.write_identity,
        who_kind=principal.kind,
        did=did[:240],
        reason=(reason[:240] if reason else None),
        payload_json=json.dumps(payload or {}, ensure_ascii=False, sort_keys=True),
        at=utcnow(),
    )
    db.add(event)
    return event


def event_to_dict(event: DistillEvent) -> dict[str, Any]:
    try:
        payload = json.loads(event.payload_json or "{}")
    except json.JSONDecodeError:
        payload = {}
    return {
        "id": event.id,
        "candidate_id": event.candidate_id,
        "seq": event.seq,
        "event_type": event.event_type,
        "who": event.who,
        "who_kind": event.who_kind,
        "did": event.did,
        "reason": event.reason,
        "payload": payload,
        "at": _iso(event.at),
    }


def candidate_to_dict(
    db: Session, candidate: DistillCandidate, *, include_events: bool = True
) -> dict[str, Any]:
    body: dict[str, Any] = {
        "id": candidate.id,
        "source_session_id": candidate.source_session_id,
        "summary": candidate.summary,
        "origin_ref": candidate.origin_ref,
        "status": candidate.status,
        "cooldown_until": _iso(candidate.cooldown_until),
        "created_by": candidate.created_by,
        "created_at": _iso(candidate.created_at),
        "decided_by": candidate.decided_by,
        "decided_at": _iso(candidate.decided_at),
        "decision_note": candidate.decision_note,
        "promoted_entry_id": candidate.promoted_entry_id,
    }
    if include_events:
        events = list(
            db.execute(
                select(DistillEvent)
                .where(DistillEvent.candidate_id == candidate.id)
                .order_by(DistillEvent.seq)
            ).scalars()
        )
        body["events"] = [event_to_dict(event) for event in events]
    return body


def get_candidate(db: Session, candidate_id: int) -> DistillCandidate | None:
    return db.get(DistillCandidate, candidate_id)


def list_candidates(
    db: Session, *, status: str | None = None
) -> list[DistillCandidate]:
    query = select(DistillCandidate).order_by(DistillCandidate.id.desc())
    if status is not None:
        if status not in ACTIVE_STATUSES:
            raise ProtocolError(
                f"status must be one of {', '.join(ACTIVE_STATUSES)}"
            )
        query = query.where(DistillCandidate.status == status)
    return list(db.execute(query).scalars())


def register_candidate(
    db: Session,
    principal: Principal,
    *,
    summary: str,
    source_session_id: int | None = None,
    origin_ref: str | None = None,
    cooldown_hours: int | None = None,
) -> DistillCandidate:
    _assert_not_viewer(principal)
    if principal.kind == "channel":
        raise Forbidden("channel credentials cannot register distillation candidates")

    text = (summary or "").strip()
    if not text:
        raise ProtocolError("summary is required")
    if len(text) > SUMMARY_MAX:
        raise ProtocolError(f"summary must be at most {SUMMARY_MAX} characters")

    ref = origin_ref.strip() if origin_ref else None
    if ref is not None and len(ref) > ORIGIN_REF_MAX:
        raise ProtocolError(f"origin_ref must be at most {ORIGIN_REF_MAX} characters")

    if source_session_id is not None:
        session = db.get(RuntimeSession, source_session_id)
        if session is None:
            raise ProtocolError(f"source session not found: {source_session_id}")

    hours = cooldown_hours_default() if cooldown_hours is None else cooldown_hours
    if hours < 0:
        raise ProtocolError("cooldown_hours must be a non-negative integer")

    now = utcnow()
    candidate = DistillCandidate(
        source_session_id=source_session_id,
        summary=text,
        origin_ref=ref,
        status=STATUS_PENDING,
        cooldown_until=now + dt.timedelta(hours=hours),
        created_by=principal.write_identity,
        created_at=now,
        decision_note="",
    )
    db.add(candidate)
    db.flush()
    _append_event(
        db,
        candidate,
        event_type=EVENT_REGISTERED,
        principal=principal,
        did="registered distillation candidate",
        payload={
            "source_session_id": source_session_id,
            "cooldown_hours": hours,
            "summary_chars": len(text),
        },
    )
    db.flush()
    return candidate


def _session_fingerprint(db: Session, session_id: int | None) -> dict[str, Any] | None:
    if session_id is None:
        return None
    session = db.get(RuntimeSession, session_id)
    if session is None:
        return None
    return {
        "id": session.id,
        "title": session.title,
        "summary": session.summary,
        "content_hash": session.content_hash,
        "message_count": session.message_count,
        "messages_json": session.messages_json,
        "cursor": session.cursor,
        "privacy": session.privacy,
        "updated_at": _iso(session.updated_at),
        "synced_at": _iso(session.synced_at),
    }


def promote_candidate(
    db: Session, principal: Principal, candidate: DistillCandidate
) -> DistillCandidate:
    _assert_privileged(principal)
    if candidate.status != STATUS_PENDING:
        raise Conflict(f"candidate is already {candidate.status}")

    now = utcnow()
    cooldown = candidate.cooldown_until
    if cooldown.tzinfo is None:
        cooldown = cooldown.replace(tzinfo=dt.timezone.utc)
    if now < cooldown:
        raise Conflict(
            f"cooling gate: candidate remains in cooldown until {_iso(cooldown)}"
        )

    before = _session_fingerprint(db, candidate.source_session_id)

    name = f"distill-{candidate.id}"
    existing = db.execute(
        select(KnowledgeSource).where(KnowledgeSource.name == name)
    ).scalar()
    if existing is not None:
        raise Conflict(f"knowledge entry already exists for candidate: {name}")

    entry = KnowledgeSource(
        name=name,
        kind=KNOWLEDGE_KIND,
        location=candidate.origin_ref or "",
        docs=1,
        size_bytes=len(candidate.summary.encode("utf-8")),
        notes=candidate.summary,
    )
    db.add(entry)
    db.flush()

    candidate.status = STATUS_PROMOTED
    candidate.decided_by = principal.write_identity
    candidate.decided_at = now
    candidate.decision_note = ""
    candidate.promoted_entry_id = entry.id
    _append_event(
        db,
        candidate,
        event_type=EVENT_PROMOTED,
        principal=principal,
        did="promoted candidate to knowledge entry",
        payload={"promoted_entry_id": entry.id, "knowledge_name": name},
    )
    db.flush()

    after = _session_fingerprint(db, candidate.source_session_id)
    if before != after:
        raise ProtocolError("source session must remain unchanged by distillation")
    return candidate


def reject_candidate(
    db: Session,
    principal: Principal,
    candidate: DistillCandidate,
    *,
    note: str = "",
) -> DistillCandidate:
    _assert_privileged(principal)
    if candidate.status != STATUS_PENDING:
        raise Conflict(f"candidate is already {candidate.status}")

    reason = (note or "").strip()
    if not reason:
        raise ProtocolError("decision_note is required when rejecting")
    if len(reason) > 240:
        raise ProtocolError("decision_note must be at most 240 characters")

    before = _session_fingerprint(db, candidate.source_session_id)
    now = utcnow()
    candidate.status = STATUS_REJECTED
    candidate.decided_by = principal.write_identity
    candidate.decided_at = now
    candidate.decision_note = reason
    _append_event(
        db,
        candidate,
        event_type=EVENT_REJECTED,
        principal=principal,
        did="rejected distillation candidate",
        reason=reason,
    )
    db.flush()
    after = _session_fingerprint(db, candidate.source_session_id)
    if before != after:
        raise ProtocolError("source session must remain unchanged by distillation")
    return candidate
