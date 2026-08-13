"""Task operations on the database, enforcing Retinue protocol v0.2 semantics:
six-state machine, append-only event chain, holder-only-writes for agents.
"""

from __future__ import annotations

import base64
import binascii
import datetime as dt
import hashlib
import json
import os
import threading
from dataclasses import dataclass
from typing import Any, Iterable

from sqlalchemy import and_, func, or_, select, update
from sqlalchemy.orm import Session, aliased, raiseload

from core.protocol.task import (
    DEPENDENCY_KINDS,
    ID_RE,
    PRIORITIES,
    STATES,
    ProtocolError,
    add_acted_on_behalf_of,
    drift_report,
    fold_task_events,
    state_payload,
    utc_timestamp,
    validate_ledger_text,
    validate_transition,
)

from .db import (
    Actor,
    RuntimeSession,
    Task,
    TaskAttempt,
    TaskDependency,
    TaskEvent,
    WorkdirLock,
    utcnow,
)


REVIEW_DECISIONS = ("accepted", "needs_info", "declined")
ATTEMPT_OUTCOMES = ("succeeded", "failed", "cancelled")
ORCHESTRATION_ACTOR = "orchestration"
LEASE_FAILURE_TRANSIENT = frozenset(
    {
        "lost-heartbeat",
        "start-timeout",
        "queue-timeout",
        "stuck",
        "disconnect",
        "precheck",
    }
)
LEASE_FAILURE_SEMANTIC = frozenset(
    {"quota", "credential", "context-overflow", "configuration", "semantic"}
)
LEASE_FAILURE_POLLUTED = frozenset({"context-overflow", "credential", "configuration"})
ATTEMPT_TRIGGER_SOURCES = (
    "claim",
    "retry",
    "human",
    "sweep",
    "precheck",
    "worker",
)
ATTEMPT_FAILURE_CLASSES = ("transient", "semantic", "precheck")

# Sentinel for "argument not passed", so an explicit None can clear a value.
UNSET: Any = object()


@dataclass(frozen=True)
class LeaseSettings:
    """Multica-shaped production defaults; override with RETINUE_LEASE_*."""

    heartbeat_seconds: int = 15
    lost_seconds: int = 180
    start_timeout_seconds: int = 300
    unclaimed_seconds: int = 7200
    retry_limit: int = 3


def _env_int(name: str, default: int, *, minimum: int = 1) -> int:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError:
        return default
    return max(minimum, value)


def lease_settings() -> LeaseSettings:
    return LeaseSettings(
        heartbeat_seconds=_env_int("RETINUE_LEASE_HEARTBEAT_SECONDS", 15),
        lost_seconds=_env_int("RETINUE_LEASE_LOST_SECONDS", 180),
        start_timeout_seconds=_env_int("RETINUE_LEASE_START_TIMEOUT_SECONDS", 300),
        unclaimed_seconds=_env_int("RETINUE_LEASE_UNCLAIMED_SECONDS", 7200),
        retry_limit=_env_int("RETINUE_LEASE_RETRY_LIMIT", 3),
    )


def parse_due_date(value: str | dt.date | None) -> dt.date | None:
    """Normalize a calendar-day deadline; None or blank clears it."""
    if value is None:
        return None
    if isinstance(value, dt.date) and not isinstance(value, dt.datetime):
        return value
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        try:
            return dt.date.fromisoformat(text)
        except ValueError:
            pass
    raise ProtocolError("due_at must be a calendar date in YYYY-MM-DD form")


class Forbidden(ProtocolError):
    """Raised when the acting principal may not perform the operation."""


class Conflict(ProtocolError):
    """Raised when a concurrent mutation won the race (e.g. claim taken)."""


# Serializes review-event sequence allocation within this process. The event
# sequence unique constraint remains the cross-process backstop for that path.
_write_lock = threading.Lock()


def _now_iso() -> str:
    return utc_timestamp()


def next_task_id(db: Session) -> str:
    today = dt.date.today().strftime("%Y%m%d")
    prefix = f"task-{today}-"
    latest = db.execute(
        select(func.max(Task.id)).where(Task.id.like(prefix + "%"))
    ).scalar()
    serial = int(latest.rsplit("-", 1)[1]) + 1 if latest else 1
    if serial > 999:
        raise ProtocolError("daily task id space exhausted (999 cards)")
    return f"{prefix}{serial:03d}"


def _require_actor(db: Session, actor_id: str, field: str) -> Actor:
    actor = db.get(Actor, actor_id)
    if actor is None or actor.disabled:
        raise ProtocolError(f"{field}: unknown or disabled actor {actor_id!r}")
    return actor


def task_to_dict(
    task: Task, dependencies: dict[str, list[dict[str, Any]]] | None = None
) -> dict[str, Any]:
    result = task_summary_to_dict(task, dependencies)
    proposal = None
    for event in task.events:
        payload = _payload(event)
        candidate = payload.get("roster_proposal")
        if event.event_type == "roster_proposal" and isinstance(candidate, dict):
            proposal = candidate
            break
    result.update({
        "chain": [
            {
                "who": e.who,
                "did": e.did,
                "at": e.at,
                "from_status": e.from_status,
                "to_status": e.to_status,
                "from_holder": e.from_holder,
                "to_holder": e.to_holder,
                "type": e.event_type,
                "id": e.event_key,
                "parent_id": e.parent_key,
                "payload": _payload(e),
            }
            for e in task.events
        ],
        "attempts": [attempt_to_dict(attempt) for attempt in task.attempts],
        "reviews": reviews_from_task(task),
        "proposal": proposal,
    })
    return result


def attempt_to_dict(attempt: TaskAttempt) -> dict[str, Any]:
    return {
        "id": attempt.attempt_key,
        "seq": attempt.seq,
        "reporter": {
            "kind": attempt.reporter_kind,
            "id": attempt.reporter_id,
            "duty": attempt.duty,
        },
        "started_at": attempt.started_at,
        "ended_at": attempt.ended_at,
        "outcome": attempt.outcome,
        "reason": attempt.reason,
        "exit_status": attempt.exit_status,
        "reported_at": attempt.reported_at,
        "lease_term": attempt.lease_term,
        "trigger_source": attempt.trigger_source,
        "session_ref": attempt.session_ref,
        "checkpoint_ref": attempt.checkpoint_ref,
        "failure_class": attempt.failure_class,
        "workdir_key": attempt.workdir_key,
    }


def _attempt_time(value: dt.datetime, field: str) -> str:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ProtocolError(f"{field} must include a timezone")
    return value.astimezone(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _attempt_reason(value: str | None, outcome: str) -> str | None:
    reason = (value or "").strip()
    if outcome == "failed" and not reason:
        raise ProtocolError("a failed attempt requires a short reason")
    if outcome == "succeeded" and reason:
        raise ProtocolError("a succeeded attempt must not include a failure reason")
    if not reason:
        return None
    return validate_ledger_text(reason, "attempt reason")


def _attempt_key(
    task_id: str,
    reporter_kind: str,
    reporter_id: str,
    duty: str | None,
    idempotency_key: str,
) -> str:
    digest = hashlib.sha256(
        "\0".join(
            (task_id, reporter_kind, reporter_id, duty or "", idempotency_key)
        ).encode()
    ).hexdigest()[:32]
    return f"attempt-{digest}"


def append_attempt(
    db: Session,
    task: Task,
    *,
    reporter_kind: str,
    reporter_id: str,
    duty: str | None,
    outcome: str,
    started_at: dt.datetime,
    ended_at: dt.datetime,
    reason: str | None,
    exit_status: int | None,
    idempotency_key: str,
    lease_term: int | None = None,
    trigger_source: str | None = None,
    session_ref: str | None = None,
    checkpoint_ref: str | None = None,
    failure_class: str | None = None,
    workdir_key: str | None = None,
    now: dt.datetime | None = None,
    is_privileged: bool = False,
) -> tuple[TaskAttempt, bool]:
    """Append one completed attempt without mutating task state or its chain."""
    if reporter_kind not in {"actor", "operator", "node"}:
        raise ProtocolError("unknown attempt reporter kind")
    if outcome not in ATTEMPT_OUTCOMES:
        raise ProtocolError(f"outcome must be one of: {', '.join(ATTEMPT_OUTCOMES)}")
    clean_reason = _attempt_reason(reason, outcome)
    if outcome != "failed" and exit_status is not None:
        raise ProtocolError("exit_status is only valid for a failed attempt")
    start = _attempt_time(started_at, "started_at")
    end = _attempt_time(ended_at, "ended_at")
    if ended_at.astimezone(dt.timezone.utc) < started_at.astimezone(dt.timezone.utc):
        raise ProtocolError("ended_at must not be before started_at")
    if trigger_source is not None and trigger_source not in ATTEMPT_TRIGGER_SOURCES:
        raise ProtocolError(
            f"trigger_source must be one of: {', '.join(ATTEMPT_TRIGGER_SOURCES)}"
        )
    if failure_class is not None and failure_class not in ATTEMPT_FAILURE_CLASSES:
        raise ProtocolError(
            f"failure_class must be one of: {', '.join(ATTEMPT_FAILURE_CLASSES)}"
        )
    clean_session = _optional_ref(session_ref, "session_ref")
    clean_checkpoint = _optional_ref(checkpoint_ref, "checkpoint_ref")
    clean_workdir = _optional_workdir_key(workdir_key)
    if reporter_kind == "actor" and not is_privileged:
        assert_lease_write(task, lease_term, is_privileged=False, now=now)

    key = _attempt_key(task.id, reporter_kind, reporter_id, duty, idempotency_key)
    expected = {
        "reporter_kind": reporter_kind,
        "reporter_id": reporter_id,
        "duty": duty,
        "outcome": outcome,
        "reason": clean_reason,
        "exit_status": exit_status,
        "started_at": start,
        "ended_at": end,
        "lease_term": lease_term,
        "trigger_source": trigger_source,
        "session_ref": clean_session,
        "checkpoint_ref": clean_checkpoint,
        "failure_class": failure_class,
        "workdir_key": clean_workdir,
    }
    with _write_lock:
        existing = db.execute(
            select(TaskAttempt).where(TaskAttempt.attempt_key == key)
        ).scalar()
        if existing is not None:
            actual = {name: getattr(existing, name) for name in expected}
            if existing.task_id == task.id and actual == expected:
                return existing, False
            raise Conflict("idempotency key already used for another attempt")
        db.refresh(task)
        attempt = TaskAttempt(
            task_id=task.id,
            seq=(task.attempts[-1].seq if task.attempts else 0) + 1,
            attempt_key=key,
            reported_at=_now_iso(),
            **expected,
        )
        task.attempts.append(attempt)
        db.flush()
        return attempt, True


def task_summary_to_dict(
    task: Task, dependencies: dict[str, list[dict[str, Any]]] | None = None
) -> dict[str, Any]:
    """Project fields used by task lists without touching the event relationship."""
    relation = dependencies or {"blocked_by": [], "blocks": []}
    blocked_by = relation.get("blocked_by", [])
    return {
        "id": task.id,
        "title": task.title,
        "created_by": task.created_by,
        "dept": task.dept,
        "priority": task.priority,
        "status": task.status,
        "holder": task.holder,
        "blocked_reason": task.blocked_reason,
        "next": task.next_holder,
        "blocked_by": blocked_by,
        "blocks": relation.get("blocks", []),
        "depends_on": [item["id"] for item in blocked_by],
        "due_at": task.due_at.isoformat() if task.due_at else None,
        "ready": dependencies is not None
        and task.status == "queued"
        and all(item["status"] == "done" for item in blocked_by),
        "acceptance": task.acceptance,
        "refs": task.refs,
        "progress": task.progress,
        "open_dispatch": task.open_dispatch,
        "pipeline": json.loads(task.pipeline_json) if task.pipeline_json else None,
        "pipeline_stage": task.pipeline_stage,
        "archived": task.archived,
        "created_at": task.created_at.isoformat() if task.created_at else None,
        "updated_at": task.updated_at.isoformat() if task.updated_at else None,
        "lease": lease_projection(task),
    }


def task_row_state(task: Task) -> dict[str, Any]:
    """Project the eight state-bearing row fields covered by the chain."""
    return {
        "priority": task.priority,
        "acceptance": task.acceptance,
        "dept": task.dept,
        "refs": task.refs,
        "progress": task.progress,
        "blocked_reason": task.blocked_reason,
        "holder": task.holder,
        "status": task.status,
    }


def _event_for_fold(event: TaskEvent) -> dict[str, Any]:
    try:
        payload: Any = json.loads(event.payload_json or "{}")
    except (TypeError, json.JSONDecodeError):
        payload = event.payload_json
    return {
        "who": event.who,
        "from_status": event.from_status,
        "to_status": event.to_status,
        "from_holder": event.from_holder,
        "to_holder": event.to_holder,
        "payload": payload,
    }


def fold_stored_task(task: Task):
    """Fold a database task exclusively from its ordered event relationship."""
    return fold_task_events(_event_for_fold(event) for event in task.events)


def _missing_event_sequence(task: Task) -> list[int]:
    """Sequence numbers absent from a chain that should run 1..n without holes.

    Rewriting an event is already caught by the fold, which requires each event's `before`
    values to match the preceding event's `after` values. Removing one is not: an event whose
    only content was its note leaves the surrounding chain linking perfectly, so every field
    comparison still agrees. The sequence is the only evidence of how many events existed.
    """
    seen = sorted(event.seq for event in task.events)
    if not seen:
        return []
    return [number for number in range(1, seen[-1] + 1) if number not in set(seen)]


def audit_stored_task(task: Task) -> dict[str, Any]:
    """Return a read-only comparison of a task row with its folded chain."""
    report = drift_report(fold_stored_task(task), task_row_state(task))
    missing = _missing_event_sequence(task)
    if missing:
        # A hole means events are gone, so the remaining chain cannot be treated as the
        # whole history even when every field it does carry agrees with the row.
        report["missing_event_sequence"] = missing
        report["in_sync"] = False
        report["status"] = "invalid"
    return {"task_id": task.id, **report}


def dependency_graph(
    db: Session, task_ids: Iterable[str]
) -> dict[str, dict[str, list[dict[str, Any]]]]:
    """Batch both directions of task edges without loading task event chains."""
    ids = list(dict.fromkeys(task_ids))
    graph = {task_id: {"blocked_by": [], "blocks": []} for task_id in ids}
    if not ids:
        return graph

    prerequisite = aliased(Task)
    for dependent_id, kind, task_id, title, status in db.execute(
        select(
            TaskDependency.dependent_id,
            TaskDependency.kind,
            prerequisite.id,
            prerequisite.title,
            prerequisite.status,
        )
        .join(prerequisite, prerequisite.id == TaskDependency.prerequisite_id)
        .where(TaskDependency.dependent_id.in_(ids))
        .order_by(TaskDependency.dependent_id, prerequisite.id)
    ):
        graph[dependent_id]["blocked_by"].append(
            {"id": task_id, "title": title, "status": status, "kind": kind}
        )

    dependent = aliased(Task)
    for prerequisite_id, kind, task_id, title, status in db.execute(
        select(
            TaskDependency.prerequisite_id,
            TaskDependency.kind,
            dependent.id,
            dependent.title,
            dependent.status,
        )
        .join(dependent, dependent.id == TaskDependency.dependent_id)
        .where(TaskDependency.prerequisite_id.in_(ids))
        .order_by(TaskDependency.prerequisite_id, dependent.id)
    ):
        graph[prerequisite_id]["blocks"].append(
            {"id": task_id, "title": title, "status": status, "kind": kind}
        )
    return graph


def _payload(event: TaskEvent) -> dict[str, Any]:
    try:
        value = json.loads(event.payload_json or "{}")
    except (TypeError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def reviews_from_task(task: Task) -> list[dict[str, Any]]:
    """Project review events without inventing a second task state."""
    reviews: dict[str, dict[str, Any]] = {}
    ordered: list[dict[str, Any]] = []
    for event in task.events:
        payload = _payload(event)
        if event.event_type == "review_comment" and event.event_key:
            item = {
                "id": event.event_key,
                "author": event.who,
                "body": str(payload.get("body") or ""),
                "created_at": event.at,
                "artifact_ref": payload.get("artifact_ref"),
                "decision": None,
                "replies": [],
                "evidence_refs": [],
            }
            reviews[event.event_key] = item
            ordered.append(item)
        elif event.event_type == "review_reply" and event.parent_key:
            item = reviews.get(event.parent_key)
            if item is None:
                continue
            evidence = [
                str(ref) for ref in payload.get("evidence_refs", [])
                if isinstance(ref, str) and ref.strip()
            ]
            reply = {
                "id": event.event_key,
                "author": event.who,
                "body": str(payload.get("body") or ""),
                "created_at": event.at,
                "decision": payload.get("decision"),
                "evidence_refs": evidence,
            }
            item["replies"].append(reply)
            item["decision"] = reply["decision"]
            for ref in evidence:
                if ref not in item["evidence_refs"]:
                    item["evidence_refs"].append(ref)
    return ordered


def _review_key(prefix: str, task_id: str, who: str, idempotency_key: str) -> str:
    digest = hashlib.sha256(
        f"{prefix}\0{task_id}\0{who}\0{idempotency_key}".encode()
    ).hexdigest()[:24]
    return f"{prefix}-{digest}"


def _clean_review_text(value: str, field: str) -> str:
    text = value.replace("\r\n", "\n").replace("\r", "\n").strip()
    if not text:
        raise ProtocolError(f"{field} must be non-empty")
    if len(text) > 1200:
        raise ProtocolError(f"{field} must not exceed 1200 characters")
    if any(ord(char) < 32 and char not in "\n\t" for char in text):
        raise ProtocolError(f"{field} contains unsupported control characters")
    return text


def _append_review_event(
    db: Session,
    task: Task,
    *,
    who: str,
    event_type: str,
    event_key: str,
    parent_key: str | None,
    payload: dict[str, Any],
) -> tuple[TaskEvent, bool]:
    canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True)
    with _write_lock:
        existing = db.execute(
            select(TaskEvent).where(TaskEvent.event_key == event_key)
        ).scalar()
        if existing is not None:
            if (
                existing.task_id == task.id
                and existing.event_type == event_type
                and existing.parent_key == parent_key
                and existing.payload_json == canonical
            ):
                return existing, False
            raise Conflict("idempotency key already used for another review event")
        db.refresh(task)
        event = TaskEvent(
            seq=(task.events[-1].seq if task.events else 0) + 1,
            who=who,
            did="质检意见" if event_type == "review_comment" else "质检回复",
            at=_now_iso(),
            from_status=task.status,
            to_status=task.status,
            from_holder=task.holder,
            to_holder=task.holder,
            event_type=event_type,
            event_key=event_key,
            parent_key=parent_key,
            payload_json=canonical,
        )
        task.events.append(event)
        db.flush()
        return event, True


def append_review_comment(
    db: Session,
    task: Task,
    *,
    who: str,
    idempotency_key: str,
    body: str,
    artifact_ref: str | None = None,
) -> tuple[TaskEvent, bool]:
    """Append a human QC comment, including after task completion."""
    actor = _require_actor(db, who, "review author")
    if actor.kind != "human":
        raise Forbidden("review comments must be submitted by a human actor")
    text = _clean_review_text(body, "review body")
    artifact = (artifact_ref or "").strip() or None
    if artifact and len(artifact) > 1024:
        raise ProtocolError("artifact_ref must not exceed 1024 characters")
    key = _review_key("review", task.id, who, idempotency_key)
    return _append_review_event(
        db,
        task,
        who=who,
        event_type="review_comment",
        event_key=key,
        parent_key=None,
        payload={"body": text, "artifact_ref": artifact},
    )


def append_review_reply(
    db: Session,
    task: Task,
    *,
    who: str,
    review_id: str,
    idempotency_key: str,
    body: str,
    decision: str,
    evidence_refs: Iterable[str] = (),
) -> tuple[TaskEvent, bool]:
    """Append an AI/operator reply without changing task state or holder."""
    _require_actor(db, who, "review responder")
    if decision not in REVIEW_DECISIONS:
        raise ProtocolError(
            f"decision must be one of: {', '.join(REVIEW_DECISIONS)}"
        )
    comment = db.execute(
        select(TaskEvent).where(
            TaskEvent.task_id == task.id,
            TaskEvent.event_key == review_id,
            TaskEvent.event_type == "review_comment",
        )
    ).scalar()
    if comment is None:
        raise ProtocolError(f"review not found on {task.id}: {review_id}")
    text = _clean_review_text(body, "reply body")
    evidence: list[str] = []
    for ref in evidence_refs:
        value = str(ref).strip()
        if not value:
            continue
        if len(value) > 1024:
            raise ProtocolError("evidence refs must not exceed 1024 characters")
        if value not in evidence:
            evidence.append(value)
    if len(evidence) > 20:
        raise ProtocolError("at most 20 evidence refs are allowed")
    key = _review_key("reply", task.id, who, idempotency_key)
    return _append_review_event(
        db,
        task,
        who=who,
        event_type="review_reply",
        event_key=key,
        parent_key=review_id,
        payload={
            "body": text,
            "decision": decision,
            "evidence_refs": evidence,
        },
    )


def _dependency_path(
    db: Session, start_id: str, goal_id: str
) -> list[str] | None:
    """Find one indexed path through prerequisites for a cycle explanation."""
    pending = [(start_id, [start_id])]
    visited: set[str] = set()
    while pending:
        current, path = pending.pop(0)
        if current == goal_id:
            return path
        if current in visited:
            continue
        visited.add(current)
        next_ids = db.execute(
            select(TaskDependency.prerequisite_id)
            .where(TaskDependency.dependent_id == current)
            .order_by(TaskDependency.prerequisite_id)
        ).scalars()
        pending.extend((task_id, [*path, task_id]) for task_id in next_ids)
    return None


def _append_dependency_event(task: Task, *, who: str, note: str) -> None:
    clean_note = note.strip()
    if not clean_note:
        raise ProtocolError("note is required for a dependency change")
    task.events.append(
        TaskEvent(
            seq=(task.events[-1].seq if task.events else 0) + 1,
            who=who,
            did=clean_note,
            at=_now_iso(),
            from_status=task.status,
            to_status=task.status,
            from_holder=task.holder,
            to_holder=task.holder,
            payload_json=json.dumps(
                state_payload(task_row_state(task), task_row_state(task)),
                ensure_ascii=False,
                sort_keys=True,
            ),
        )
    )


def add_task_dependency(
    db: Session,
    task: Task,
    *,
    prerequisite_id: str,
    kind: str,
    who: str,
    is_privileged: bool,
    note: str,
    append_event: bool = True,
) -> tuple[TaskDependency, bool]:
    """Add one acyclic finish-to-start edge to a queued card."""
    if not is_privileged and task.holder != who:
        raise Forbidden(f"holder-only-writes: {who!r} does not hold {task.id}")
    if task.status != "queued":
        raise ProtocolError("dependencies may only be changed while a card is queued")
    if kind not in DEPENDENCY_KINDS:
        raise ProtocolError(
            f"dependency kind must be one of: {', '.join(DEPENDENCY_KINDS)}"
        )
    prerequisite = db.get(Task, prerequisite_id)
    if prerequisite is None:
        raise ProtocolError(f"prerequisite card not found: {prerequisite_id}")
    if prerequisite.status == "cancelled":
        raise ProtocolError(f"cancelled card cannot be a prerequisite: {prerequisite_id}")
    existing = db.execute(
        select(TaskDependency).where(
            TaskDependency.dependent_id == task.id,
            TaskDependency.prerequisite_id == prerequisite_id,
            TaskDependency.kind == kind,
        )
    ).scalar_one_or_none()
    if existing is not None:
        return existing, False
    path = _dependency_path(db, prerequisite_id, task.id)
    if path is not None:
        cycle = [task.id, *path]
        raise ProtocolError(f"dependency cycle: {' -> '.join(cycle)}")
    edge = TaskDependency(
        dependent_id=task.id,
        prerequisite_id=prerequisite_id,
        kind=kind,
    )
    db.add(edge)
    if append_event:
        _append_dependency_event(task, who=who, note=note)
    db.flush()
    return edge, True


def remove_task_dependency(
    db: Session,
    task: Task,
    *,
    prerequisite_id: str,
    who: str,
    is_privileged: bool,
    note: str,
) -> None:
    """Remove an active edge while retaining its append-only task event."""
    if not is_privileged and task.holder != who:
        raise Forbidden(f"holder-only-writes: {who!r} does not hold {task.id}")
    if task.status != "queued":
        raise ProtocolError("dependencies may only be changed while a card is queued")
    edge = db.execute(
        select(TaskDependency).where(
            TaskDependency.dependent_id == task.id,
            TaskDependency.prerequisite_id == prerequisite_id,
            TaskDependency.kind == "blocks",
        )
    ).scalar_one_or_none()
    if edge is None:
        raise ProtocolError(f"dependency not found: {task.id} -> {prerequisite_id}")
    _append_dependency_event(task, who=who, note=note)
    db.delete(edge)
    db.flush()


def create_task(
    db: Session,
    *,
    title: str,
    created_by: str,
    holder: str,
    dept: str | None = None,
    priority: str = "none",
    acceptance: Iterable[str] = (),
    refs: Iterable[str] = (),
    depends_on: Iterable[str] = (),
    due_at: str | dt.date | None = None,
    note: str = "task created",
    open_dispatch: bool = False,
    event_type: str = "task",
    event_key: str | None = None,
    event_payload: dict[str, Any] | None = None,
    performing_agent: str | None = None,
) -> Task:
    """Create a card. With open_dispatch=True the publisher keeps the baton
    and the card is listed in the dispatch hall until an agent claims it."""
    if not title or not title.strip():
        raise ProtocolError("title must be non-empty")
    if priority not in PRIORITIES:
        raise ProtocolError(f"priority must be one of: {', '.join(PRIORITIES)}")
    normalized_dept = dept.strip() if dept and dept.strip() else None
    if normalized_dept and len(normalized_dept) > 64:
        raise ProtocolError("dept must not exceed 64 characters")
    _require_actor(db, created_by, "created_by")
    _require_actor(db, holder, "holder")
    if open_dispatch and holder != created_by:
        raise ProtocolError("an open-dispatch card cannot also assign an executor")

    acceptance_value = [a for a in acceptance if a and a.strip()]
    refs_value = list(dict.fromkeys(ref for ref in refs if ref))
    task = Task(
        id=next_task_id(db),
        title=title.strip(),
        created_by=created_by,
        dept=normalized_dept,
        priority=priority,
        status="queued",
        holder=holder,
        acceptance_json=json.dumps(acceptance_value),
        refs_json=json.dumps(refs_value),
        progress=0,
        due_at=parse_due_date(due_at),
        open_dispatch=open_dispatch,
        hall_opened_at=utcnow() if open_dispatch else None,
    )
    initial_payload = state_payload(None, task_row_state(task))
    if event_payload:
        overlap = set(initial_payload).intersection(event_payload)
        if overlap:
            raise ProtocolError(
                f"event payload cannot replace task state keys: {', '.join(sorted(overlap))}"
            )
        initial_payload.update(event_payload)
    if performing_agent is not None:
        initial_payload = add_acted_on_behalf_of(
            initial_payload,
            authorising_identity=created_by,
            performing_agent=performing_agent,
        )
    task.events.append(
        TaskEvent(
            seq=1,
            who=created_by,
            did=note,
            at=_now_iso(),
            from_status=None,
            to_status="queued",
            from_holder=None,
            to_holder=holder,
            event_type=event_type,
            event_key=event_key,
            payload_json=json.dumps(
                initial_payload, ensure_ascii=False, sort_keys=True
            ),
        )
    )
    db.add(task)
    db.flush()
    for prerequisite_id in dict.fromkeys(depends_on):
        add_task_dependency(
            db,
            task,
            prerequisite_id=prerequisite_id,
            kind="blocks",
            who=created_by,
            is_privileged=True,
            note=note,
            append_event=False,
        )
    return task


def update_task(
    db: Session,
    task: Task,
    *,
    who: str,
    is_privileged: bool,
    status: str | None = None,
    holder: str | None = None,
    dept: str | None = None,
    blocked_reason: str | None = None,
    next_holder: str | None = None,
    due_at: Any = UNSET,
    priority: str | None = None,
    acceptance: Iterable[str] | None = None,
    refs: Iterable[str] = (),
    note: str | None = None,
    progress: int | None = None,
    flow_driven: bool = False,
    performing_agent: str | None = None,
    lease_term: int | None = None,
    now: dt.datetime | None = None,
) -> Task:
    """Apply one mutation and append one chain event.

    Agents (is_privileged=False) obey holder-only-writes: they may only touch
    cards they currently hold. Admins and members may act on any card, which
    covers dispatching (派卡) and acceptance review.
    """
    if not is_privileged and task.holder != who:
        raise Forbidden(f"holder-only-writes: {who!r} does not hold {task.id}")
    if task.status in ("done", "cancelled"):
        raise ProtocolError(f"{task.status} is terminal; {task.id} can no longer be mutated")
    clock = _aware(now) or utcnow()
    if not is_privileged:
        assert_lease_write(task, lease_term, is_privileged=False, now=clock)

    old_status, old_holder = task.status, task.holder
    new_status = status or old_status
    new_holder = holder or old_holder
    state_changed = new_status != old_status
    holder_changed = new_holder != old_holder

    if state_changed and old_status == "queued" and new_status == "doing":
        unfinished = _unfinished_prerequisite_ids(db, task.id)
        if unfinished:
            raise ProtocolError(
                f"{task.id} cannot start; unfinished prerequisites: "
                f"{', '.join(unfinished)}"
            )
    if state_changed and new_status == "cancelled":
        dependents = list(
            db.execute(
                select(Task.id)
                .join(TaskDependency, TaskDependency.dependent_id == Task.id)
                .where(
                    TaskDependency.prerequisite_id == task.id,
                    Task.status.not_in(("done", "cancelled")),
                )
                .order_by(Task.id)
            ).scalars()
        )
        if dependents:
            raise ProtocolError(
                f"cannot cancel {task.id}; unfinished dependents: "
                f"{', '.join(dependents)}"
            )

    if task.pipeline_json and not flow_driven:
        try:
            stages = json.loads(task.pipeline_json)
            current_stage = stages[task.pipeline_stage]
            if not isinstance(current_stage, dict):
                raise TypeError("pipeline stage must be a mapping")
        except (
            AttributeError,
            IndexError,
            KeyError,
            TypeError,
            json.JSONDecodeError,
        ) as exc:
            raise ProtocolError(f"invalid pipeline state on {task.id}") from exc
        if holder_changed:
            raise ProtocolError("pipeline holders move only through stage actions")
        if state_changed:
            if current_stage.get("gate") == "queen":
                raise ProtocolError("queen-gate tasks move only through an approval decision")
            allowed = (
                new_status == "doing"
                and old_status in ("queued", "handoff", "blocked")
            ) or (
                old_status == "doing" and new_status == "blocked"
            )
            if not allowed:
                raise ProtocolError(
                    "pipeline stages advance only through stage-done or stage-reject"
                )

    if state_changed:
        validate_transition(old_status, new_status)
    if holder_changed:
        _require_actor(db, new_holder, "holder")
    if progress is not None:
        if (
            not isinstance(progress, int)
            or isinstance(progress, bool)
            or not 0 <= progress <= 100
        ):
            raise ProtocolError("progress must be an integer between 0 and 100")
        if new_status not in ("doing", "done") and not (
            flow_driven and new_status == "handoff" and progress == 0
        ):
            raise ProtocolError("progress is only reportable while a card is doing")
    if dept is not None and (not dept.strip() or len(dept.strip()) > 64):
        raise ProtocolError("dept must contain 1 to 64 characters")
    if priority is not None and priority not in PRIORITIES:
        raise ProtocolError(f"priority must be one of: {', '.join(PRIORITIES)}")

    acceptance_value = (
        [a for a in acceptance if a and a.strip()]
        if acceptance is not None
        else task.acceptance
    )
    refs_value = task.refs
    for ref in refs:
        if ref and ref not in refs_value:
            refs_value.append(ref)
    if new_status == "blocked":
        reason_value = (
            blocked_reason.strip()
            if blocked_reason and blocked_reason.strip()
            else task.blocked_reason
        )
        if not reason_value:
            raise ProtocolError("blocked_reason is required when entering blocked")
    else:
        if blocked_reason:
            raise ProtocolError("blocked_reason is only valid with blocked status")
        reason_value = None
    progress_value = progress if progress is not None else task.progress
    if new_status == "done":
        progress_value = 100

    due_value = parse_due_date(due_at) if due_at is not UNSET else task.due_at
    due_changed = due_value != task.due_at

    old_state = task_row_state(task)
    new_state = {
        "priority": priority if priority is not None else task.priority,
        "acceptance": acceptance_value,
        "dept": dept.strip() if dept is not None else task.dept,
        "refs": refs_value,
        "progress": progress_value,
        "blocked_reason": reason_value,
        "holder": new_holder,
        "status": new_status,
    }
    payload = state_payload(old_state, new_state)
    grant_lease = new_status == "doing" and int(task.lease_term or 0) == 0
    mark_started = new_status == "doing" and task.lease_started_at is None
    if grant_lease or mark_started:
        settings = lease_settings()
        granted = _activate_lease(
            task,
            holder=new_holder,
            now=clock,
            settings=settings,
            increment=grant_lease,
        )
        payload["lease"] = granted
    if performing_agent is not None:
        payload = add_acted_on_behalf_of(
            payload,
            authorising_identity=who,
            performing_agent=performing_agent,
        )
    changed = bool(payload["changes"])
    if changed or due_changed or next_holder is not None or note is not None:
        if not note or not note.strip():
            raise ProtocolError("note is required for a transition or progress event")
        last_seq = task.events[-1].seq if task.events else 0
        task.events.append(
            TaskEvent(
                seq=last_seq + 1,
                who=who,
                did=note.strip(),
                at=_now_iso(),
                from_status=old_status,
                to_status=new_status,
                from_holder=old_holder,
                to_holder=new_holder,
                payload_json=json.dumps(payload, ensure_ascii=False, sort_keys=True),
            )
        )

    task.status = new_state["status"]
    task.holder = new_state["holder"]
    task.progress = new_state["progress"]
    task.blocked_reason = new_state["blocked_reason"]
    if holder_changed or new_status != "queued":
        # leaving queued (or any explicit assignment) takes the card off the hall
        task.open_dispatch = False
    if priority is not None:
        task.priority = new_state["priority"]
    if dept is not None:
        task.dept = new_state["dept"]
    if acceptance is not None:
        task.acceptance_json = json.dumps(new_state["acceptance"])
    if next_holder is not None:
        task.next_holder = next_holder or None
    if due_at is not UNSET:
        task.due_at = due_value
    if new_state["refs"] != old_state["refs"]:
        task.refs_json = json.dumps(new_state["refs"])
    if new_status in ("done", "cancelled"):
        release_workdir_lock(db, task)

    db.flush()
    return task


def claim_task(
    db: Session,
    task: Task,
    *,
    claimant: str,
    note: str | None = None,
    now: dt.datetime | None = None,
    workdir_key: str | None = None,
) -> Task:
    """Take an open card off the dispatch hall: baton moves to the claimant."""
    _require_actor(db, claimant, "claimant")
    if not task.open_dispatch or task.status not in {"queued", "blocked"}:
        raise Conflict(f"task is no longer claimable: {task.id}")
    if task.holder == claimant:
        raise ProtocolError("claimant already holds this task")
    unfinished = _unfinished_prerequisite_ids(db, task.id)
    if unfinished:
        raise Conflict(
            f"task is not ready: {task.id}; unfinished prerequisites: "
            f"{', '.join(unfinished)}"
        )

    clock = _aware(now) or utcnow()
    settings = lease_settings()
    expires = clock + dt.timedelta(seconds=settings.lost_seconds)
    old_status = task.status
    old_holder = task.holder
    old_state = task_row_state(task)
    last_seq = task.events[-1].seq if task.events else 0
    claimed = db.execute(
        update(Task)
        .where(
            Task.id == task.id,
            Task.open_dispatch.is_(True),
            Task.status.in_(("queued", "blocked")),
        )
        .values(
            open_dispatch=False,
            holder=claimant,
            lease_term=Task.lease_term + 1,
            lease_claimed_at=clock,
            lease_heartbeat_at=clock,
            lease_started_at=None,
            lease_expires_at=expires,
            unclaimed_escalated=False,
        )
    )
    if claimed.rowcount == 0:
        raise Conflict(f"task is no longer claimable: {task.id}")
    db.refresh(task)
    if workdir_key:
        acquire_workdir_lock(db, task, workdir_key, clock)

    lease = {
        "action": "grant",
        "term": int(task.lease_term or 0),
        "expires_at": _iso(task.lease_expires_at),
        "retry_count": int(task.retry_count or 0),
    }
    payload = state_payload(old_state, {**old_state, "holder": claimant})
    payload["lease"] = lease
    task.events.append(
        TaskEvent(
            seq=last_seq + 1,
            who=claimant,
            did=(note or "").strip() or "接单",
            at=_now_iso(),
            from_status=old_status,
            to_status=old_status,
            from_holder=old_holder,
            to_holder=claimant,
            payload_json=json.dumps(payload, ensure_ascii=False, sort_keys=True),
        )
    )
    db.flush()
    return task


def _unfinished_prerequisite_ids(db: Session, task_id: str) -> list[str]:
    return list(
        db.execute(
            select(TaskDependency.prerequisite_id)
            .join(Task, Task.id == TaskDependency.prerequisite_id)
            .where(
                TaskDependency.dependent_id == task_id,
                Task.status != "done",
            )
            .order_by(TaskDependency.prerequisite_id)
        ).scalars()
    )


def list_tasks(
    db: Session,
    *,
    status: str | None = None,
    holder: str | None = None,
    include_archived: bool = False,
) -> list[Task]:
    query = select(Task).order_by(Task.created_at.desc(), Task.id.desc())
    if status:
        if status not in STATES:
            raise ProtocolError(f"unknown status filter: {status!r}")
        query = query.where(Task.status == status)
    if holder:
        query = query.where(Task.holder == holder)
    if not include_archived:
        query = query.where(Task.archived.is_(False))
    return list(db.execute(query).scalars())


def list_ready_tasks(
    db: Session,
    *,
    holder: str | None = None,
    include_archived: bool = False,
) -> list[Task]:
    """List queued tasks with no unfinished prerequisite, without event chains."""
    prerequisite = aliased(Task)
    unfinished = (
        select(TaskDependency.id)
        .join(prerequisite, prerequisite.id == TaskDependency.prerequisite_id)
        .where(
            TaskDependency.dependent_id == Task.id,
            prerequisite.status != "done",
        )
        .exists()
    )
    query = (
        select(Task)
        .options(raiseload(Task.events), raiseload(Task.attempts))
        .where(
            or_(
                Task.status == "queued",
                and_(Task.status == "blocked", Task.open_dispatch.is_(True)),
            ),
            ~unfinished,
        )
        .order_by(Task.created_at.desc(), Task.id.desc())
    )
    if holder:
        query = query.where(Task.holder == holder)
    if not include_archived:
        query = query.where(Task.archived.is_(False))
    return list(db.execute(query).scalars())


@dataclass(frozen=True)
class TaskPage:
    items: list[Task]
    next_cursor: str | None

    @property
    def has_more(self) -> bool:
        return self.next_cursor is not None


def _encode_task_cursor(task: Task) -> str:
    created_at = task.created_at.isoformat() if task.created_at else ""
    payload = json.dumps([created_at, task.id], separators=(",", ":")).encode()
    return base64.urlsafe_b64encode(payload).decode().rstrip("=")


def _decode_task_cursor(cursor: str) -> tuple[dt.datetime, str]:
    try:
        padding = "=" * (-len(cursor) % 4)
        payload = json.loads(base64.urlsafe_b64decode(cursor + padding))
        if not (
            isinstance(payload, list)
            and len(payload) == 2
            and isinstance(payload[0], str)
            and isinstance(payload[1], str)
            and payload[0]
            and payload[1]
        ):
            raise ValueError
        return dt.datetime.fromisoformat(payload[0]), payload[1]
    except (ValueError, TypeError, json.JSONDecodeError, binascii.Error) as exc:
        raise ProtocolError("invalid task page cursor") from exc


def list_task_summaries(
    db: Session,
    *,
    page_size: int,
    cursor: str | None = None,
    status: str | None = None,
    holder: str | None = None,
    include_archived: bool = False,
) -> TaskPage:
    """Return one keyset page while forbidding accidental event-chain loading."""
    if not 1 <= page_size <= 100:
        raise ProtocolError("page_size must be between 1 and 100")

    query = (
        select(Task)
        .options(raiseload(Task.events), raiseload(Task.attempts))
        .order_by(Task.created_at.desc(), Task.id.desc())
    )
    if status:
        if status not in STATES:
            raise ProtocolError(f"unknown status filter: {status!r}")
        query = query.where(Task.status == status)
    if holder:
        query = query.where(Task.holder == holder)
    if not include_archived:
        query = query.where(Task.archived.is_(False))
    if cursor:
        created_at, task_id = _decode_task_cursor(cursor)
        query = query.where(
            or_(
                Task.created_at < created_at,
                and_(Task.created_at == created_at, Task.id < task_id),
            )
        )

    rows = list(db.execute(query.limit(page_size + 1)).scalars())
    has_more = len(rows) > page_size
    items = rows[:page_size]
    next_cursor = _encode_task_cursor(items[-1]) if has_more else None
    return TaskPage(items=items, next_cursor=next_cursor)


def _aware(value: dt.datetime | None) -> dt.datetime | None:
    if value is None:
        return None
    if value.tzinfo is None or value.utcoffset() is None:
        return value.replace(tzinfo=dt.timezone.utc)
    return value.astimezone(dt.timezone.utc)


def _iso(value: dt.datetime | None) -> str | None:
    aware = _aware(value)
    if aware is None:
        return None
    return aware.strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _optional_ref(value: str | None, field: str) -> str | None:
    if value is None:
        return None
    text = value.strip()
    if not text:
        return None
    return validate_ledger_text(text, field, max_length=128)


def _optional_workdir_key(value: str | None) -> str | None:
    if value is None:
        return None
    text = value.strip()
    if not text:
        return None
    if not ID_RE.fullmatch(text) or len(text) > 128:
        raise ProtocolError("workdir_key must be a lowercase slug")
    return text


def lease_projection(task: Task) -> dict[str, Any]:
    return {
        "term": int(task.lease_term or 0),
        "expires_at": _iso(task.lease_expires_at),
        "heartbeat_at": _iso(task.lease_heartbeat_at),
        "claimed_at": _iso(task.lease_claimed_at),
        "started_at": _iso(task.lease_started_at),
        "retry_count": int(task.retry_count or 0),
        "failure_class": task.failure_class,
        "workdir_key": task.workdir_key,
    }


def lease_is_live(task: Task, now: dt.datetime | None = None) -> bool:
    clock = _aware(now) or utcnow()
    if int(task.lease_term or 0) <= 0:
        return False
    expires = _aware(task.lease_expires_at)
    return expires is not None and expires > clock


def assert_lease_write(
    task: Task,
    lease_term: int | None,
    *,
    is_privileged: bool,
    now: dt.datetime | None = None,
) -> None:
    """Refuse a stale or expired term. Operators may omit the term."""
    current = int(task.lease_term or 0)
    if current <= 0:
        return
    clock = _aware(now) or utcnow()
    live = lease_is_live(task, clock)
    if is_privileged and lease_term is None:
        return
    if lease_term is None:
        if not live:
            raise Conflict("lease expired; stale writer is fenced")
        return
    if int(lease_term) != current:
        raise Conflict(f"stale lease term {lease_term}; current term is {current}")
    if not live:
        raise Conflict("lease expired; stale writer is fenced")


def _activate_lease(
    task: Task,
    *,
    holder: str,
    now: dt.datetime,
    settings: LeaseSettings,
    increment: bool,
) -> dict[str, Any]:
    if increment:
        task.lease_term = int(task.lease_term or 0) + 1
        task.lease_claimed_at = now
        task.unclaimed_escalated = False
    if task.lease_started_at is None:
        task.lease_started_at = now
    task.lease_heartbeat_at = now
    task.lease_expires_at = now + dt.timedelta(seconds=settings.lost_seconds)
    return {
        "action": "grant" if increment else "start",
        "term": int(task.lease_term or 0),
        "expires_at": _iso(task.lease_expires_at),
        "holder": holder,
        "retry_count": int(task.retry_count or 0),
    }


def _append_lease_event(
    task: Task,
    *,
    who: str,
    note: str,
    old_status: str,
    new_status: str,
    old_holder: str,
    new_holder: str,
    old_state: dict[str, Any],
    new_state: dict[str, Any],
    lease: dict[str, Any],
) -> None:
    payload = state_payload(old_state, new_state)
    payload["lease"] = lease
    task.events.append(
        TaskEvent(
            seq=(task.events[-1].seq if task.events else 0) + 1,
            who=who,
            did=note,
            at=_now_iso(),
            from_status=old_status,
            to_status=new_status,
            from_holder=old_holder,
            to_holder=new_holder,
            payload_json=json.dumps(payload, ensure_ascii=False, sort_keys=True),
        )
    )


def acquire_workdir_lock(
    db: Session, task: Task, workdir_key: str, now: dt.datetime
) -> WorkdirLock:
    key = _optional_workdir_key(workdir_key)
    if key is None:
        raise ProtocolError("workdir_key must be a lowercase slug")
    existing = db.get(WorkdirLock, key)
    if existing is not None:
        if existing.task_id == task.id:
            existing.holder = task.holder
            existing.lease_term = int(task.lease_term or 0)
            existing.acquired_at = now
            task.workdir_key = key
            return existing
        other = db.get(Task, existing.task_id)
        if other is not None and lease_is_live(other, now):
            raise Conflict("workdir is busy; wait for the current run to finish")
        db.delete(existing)
        db.flush()
    lock = WorkdirLock(
        workdir_key=key,
        task_id=task.id,
        holder=task.holder,
        lease_term=int(task.lease_term or 0),
        acquired_at=now,
    )
    db.add(lock)
    task.workdir_key = key
    db.flush()
    return lock


def release_workdir_lock(db: Session, task: Task) -> None:
    if not task.workdir_key:
        return
    lock = db.get(WorkdirLock, task.workdir_key)
    if lock is not None and lock.task_id == task.id:
        db.delete(lock)
        db.flush()


def retry_plan(task: Task, last_attempt: TaskAttempt | None) -> dict[str, Any]:
    """Decide whether a retry keeps the session or only the work directory."""
    workdir = None
    session_ref = None
    checkpoint = None
    polluted = False
    if last_attempt is not None:
        workdir = last_attempt.workdir_key or task.workdir_key
        checkpoint = last_attempt.checkpoint_ref
        kind = last_attempt.failure_class or task.failure_class
        polluted = kind in LEASE_FAILURE_POLLUTED
        if not polluted:
            session_ref = last_attempt.session_ref
    else:
        workdir = task.workdir_key
        polluted = (task.failure_class or "") in LEASE_FAILURE_POLLUTED
    return {
        "workdir_key": workdir,
        "resume_session": session_ref,
        "new_session": polluted or session_ref is None,
        "checkpoint_ref": checkpoint,
        "polluted": polluted,
    }


def heartbeat_task(
    db: Session,
    task: Task,
    *,
    who: str,
    lease_term: int,
    started: bool = False,
    workdir_key: str | None = None,
    now: dt.datetime | None = None,
) -> Task:
    """Renew a live lease. Liveness is heartbeat-only; long work has no cap."""
    if task.holder != who:
        raise Forbidden(f"holder-only-writes: {who!r} does not hold {task.id}")
    if task.status in ("done", "cancelled"):
        raise ProtocolError(f"{task.status} is terminal; {task.id} can no longer be mutated")
    clock = _aware(now) or utcnow()
    assert_lease_write(task, lease_term, is_privileged=False, now=clock)
    settings = lease_settings()
    task.lease_heartbeat_at = clock
    task.lease_expires_at = clock + dt.timedelta(seconds=settings.lost_seconds)
    if workdir_key:
        acquire_workdir_lock(db, task, workdir_key, clock)
    if started and task.lease_started_at is None:
        old_status = task.status
        old_holder = task.holder
        old_state = task_row_state(task)
        new_status = old_status
        if old_status == "queued":
            unfinished = _unfinished_prerequisite_ids(db, task.id)
            if unfinished:
                raise ProtocolError(
                    f"{task.id} cannot start; unfinished prerequisites: "
                    f"{', '.join(unfinished)}"
                )
            validate_transition(old_status, "doing")
            new_status = "doing"
        elif old_status == "blocked":
            validate_transition(old_status, "doing")
            new_status = "doing"
            task.blocked_reason = None
        task.lease_started_at = clock
        task.status = new_status
        if new_status != "queued":
            task.open_dispatch = False
        if new_status != old_status:
            new_state = {**old_state, "status": new_status, "blocked_reason": None}
            _append_lease_event(
                task,
                who=who,
                note="lease start; execution is live",
                old_status=old_status,
                new_status=new_status,
                old_holder=old_holder,
                new_holder=old_holder,
                old_state=old_state,
                new_state=new_state,
                lease={
                    "action": "start",
                    "term": int(task.lease_term or 0),
                    "expires_at": _iso(task.lease_expires_at),
                    "retry_count": int(task.retry_count or 0),
                },
            )
    db.flush()
    return task


def _reclaim_target(task: Task) -> tuple[str, str, str | None]:
    """Return (status, holder, blocked_reason) after an automatic reclaim.

    ``doing → blocked`` is the legal path back to a claimable hall card.
    ``handoff → blocked`` is not a legal transition, so a lost handoff stays
    in handoff and is parked with the publisher for a human retry.
    """
    publisher = task.created_by
    if task.status == "doing":
        return "blocked", publisher, "lease expired:lost-heartbeat"
    if task.status == "handoff":
        return "handoff", publisher, None
    if task.status == "blocked":
        return "blocked", publisher, task.blocked_reason or "lease expired:lost-heartbeat"
    return "queued", publisher, None


def _apply_reclaim_row(
    db: Session,
    task: Task,
    *,
    who: str,
    note: str,
    failure_class: str,
    reason: str,
    now: dt.datetime,
    reopen: bool,
) -> None:
    old_status = task.status
    old_holder = task.holder
    old_state = task_row_state(task)
    new_status, new_holder, blocked_reason = _reclaim_target(task)
    if not reopen:
        new_holder = task.created_by
        if old_status == "doing":
            validate_transition(old_status, "blocked")
            new_status = "blocked"
            blocked_reason = reason
        elif old_status == "blocked":
            new_status = "blocked"
            blocked_reason = reason
        elif old_status == "handoff":
            new_status = "handoff"
            blocked_reason = None
        else:
            new_status = "queued"
            blocked_reason = None
    elif new_status != old_status:
        validate_transition(old_status, new_status)

    _require_actor(db, new_holder, "holder")
    new_state = {
        **old_state,
        "status": new_status,
        "holder": new_holder,
        "blocked_reason": blocked_reason,
    }
    task.status = new_status
    task.holder = new_holder
    task.blocked_reason = blocked_reason
    task.open_dispatch = reopen
    task.hall_opened_at = now if reopen else task.hall_opened_at
    task.unclaimed_escalated = False if reopen else task.unclaimed_escalated
    task.lease_expires_at = None
    task.lease_heartbeat_at = None
    task.lease_claimed_at = None
    task.lease_started_at = None
    task.failure_class = failure_class
    release_workdir_lock(db, task)
    _append_lease_event(
        task,
        who=who,
        note=note,
        old_status=old_status,
        new_status=new_status,
        old_holder=old_holder,
        new_holder=new_holder,
        old_state=old_state,
        new_state=new_state,
        lease={
            "action": "reclaim" if reopen else "escalate",
            "term": int(task.lease_term or 0),
            "reason": reason,
            "failure_class": failure_class,
            "retry_count": int(task.retry_count or 0),
            "reopen": reopen,
        },
    )


def _should_reclaim(task: Task, now: dt.datetime, settings: LeaseSettings) -> str | None:
    if task.status in ("done", "cancelled"):
        return None
    if task.open_dispatch:
        return None
    if int(task.lease_term or 0) <= 0:
        return None
    expires = _aware(task.lease_expires_at)
    if expires is not None and expires <= now:
        return "lost-heartbeat"
    claimed = _aware(task.lease_claimed_at)
    if (
        task.lease_started_at is None
        and claimed is not None
        and claimed + dt.timedelta(seconds=settings.start_timeout_seconds) <= now
    ):
        return "start-timeout"
    return None


def reclaim_expired_leases(
    db: Session,
    *,
    now: dt.datetime | None = None,
    settings: LeaseSettings | None = None,
) -> list[dict[str, Any]]:
    """Sweep lost or never-started leases. Safe to call from any request."""
    clock = _aware(now) or utcnow()
    config = settings or lease_settings()
    results: list[dict[str, Any]] = []
    held = list(
        db.execute(
            select(Task).where(
                Task.lease_term > 0,
                Task.open_dispatch.is_(False),
                Task.status.not_in(("done", "cancelled")),
            )
        ).scalars()
    )
    for task in held:
        reason = _should_reclaim(task, clock, config)
        if reason is None:
            continue
        next_count = int(task.retry_count or 0) + 1
        # Handoff cannot legally become blocked, so it is never returned to
        # the hall; a human retry moves handoff → doing.
        reopen = next_count <= config.retry_limit and task.status != "handoff"
        if not reopen:
            task.retry_count = next_count
            over_limit = next_count > config.retry_limit
            _apply_reclaim_row(
                db,
                task,
                who=ORCHESTRATION_ACTOR,
                note=(
                    "retry limit reached; escalated for a human"
                    if over_limit
                    else f"lease expired ({reason}); parked for a human retry"
                ),
                failure_class="transient",
                reason=(
                    f"retry limit reached after {reason}"
                    if over_limit
                    else reason
                ),
                now=clock,
                reopen=False,
            )
            results.append(
                {"task_id": task.id, "action": "escalate", "reason": reason}
            )
            continue
        task.retry_count = next_count
        _apply_reclaim_row(
            db,
            task,
            who=ORCHESTRATION_ACTOR,
            note=f"lease expired ({reason}); returned to the dispatch hall",
            failure_class="transient",
            reason=reason,
            now=clock,
            reopen=True,
        )
        results.append({"task_id": task.id, "action": "reclaim", "reason": reason})

    hall = list(
        db.execute(
            select(Task).where(
                Task.open_dispatch.is_(True),
                Task.unclaimed_escalated.is_(False),
                Task.status.in_(("queued", "blocked")),
            )
        ).scalars()
    )
    window = dt.timedelta(seconds=config.unclaimed_seconds)
    for task in hall:
        opened = _aware(task.hall_opened_at) or _aware(task.created_at)
        if opened is None or opened + window > clock:
            continue
        task.unclaimed_escalated = True
        if task.priority in ("none", "low", "medium"):
            old_state = task_row_state(task)
            task.priority = "high"
            new_state = {**old_state, "priority": "high"}
        else:
            old_state = task_row_state(task)
            new_state = old_state
        _append_lease_event(
            task,
            who=ORCHESTRATION_ACTOR,
            note="unclaimed for two hours; left on the hall and escalated",
            old_status=task.status,
            new_status=task.status,
            old_holder=task.holder,
            new_holder=task.holder,
            old_state=old_state,
            new_state=new_state,
            lease={
                "action": "unclaimed-escalate",
                "term": int(task.lease_term or 0),
                "retry_count": int(task.retry_count or 0),
            },
        )
        results.append(
            {"task_id": task.id, "action": "unclaimed-escalate", "reason": "unclaimed"}
        )
    db.flush()
    return results


def escalate_task(
    db: Session,
    task: Task,
    *,
    who: str,
    note: str,
    reason: str,
    failure_class: str = "semantic",
    lease_term: int | None = None,
    is_privileged: bool = False,
    now: dt.datetime | None = None,
) -> Task:
    """Stop automatic retry and leave a human-visible chain event."""
    if task.status in ("done", "cancelled"):
        raise ProtocolError(f"{task.status} is terminal; {task.id} can no longer be mutated")
    clock = _aware(now) or utcnow()
    if not is_privileged and task.holder != who:
        raise Forbidden(f"holder-only-writes: {who!r} does not hold {task.id}")
    if not is_privileged:
        assert_lease_write(task, lease_term, is_privileged=False, now=clock)
    clean_note = validate_ledger_text(note.strip(), "note")
    clean_reason = validate_ledger_text(reason.strip(), "reason")
    old_status = task.status
    old_holder = task.holder
    old_state = task_row_state(task)
    new_status = old_status
    blocked_reason = clean_reason
    if old_status == "doing":
        validate_transition(old_status, "blocked")
        new_status = "blocked"
    elif old_status == "blocked":
        new_status = "blocked"
    else:
        # queued / handoff cannot legally become blocked; park in place.
        blocked_reason = None
    new_holder = old_holder
    new_state = {
        **old_state,
        "status": new_status,
        "blocked_reason": blocked_reason,
    }
    task.status = new_status
    task.blocked_reason = blocked_reason
    task.open_dispatch = False
    task.failure_class = failure_class
    task.lease_expires_at = None
    task.lease_heartbeat_at = None
    release_workdir_lock(db, task)
    _append_lease_event(
        task,
        who=who,
        note=clean_note,
        old_status=old_status,
        new_status=new_status,
        old_holder=old_holder,
        new_holder=new_holder,
        old_state=old_state,
        new_state=new_state,
        lease={
            "action": "escalate",
            "term": int(task.lease_term or 0),
            "reason": clean_reason,
            "failure_class": failure_class,
            "retry_count": int(task.retry_count or 0),
        },
    )
    db.flush()
    return task


def retry_task(
    db: Session,
    task: Task,
    *,
    who: str,
    note: str,
    is_privileged: bool,
    workdir_key: str | None = None,
    now: dt.datetime | None = None,
) -> Task:
    """Human retry after a cause is fixed: new term, retry counter reset."""
    if not is_privileged and task.holder != who:
        raise Forbidden(f"holder-only-writes: {who!r} does not hold {task.id}")
    if task.status in ("done", "cancelled"):
        raise ProtocolError(f"{task.status} is terminal; {task.id} can no longer be mutated")
    clock = _aware(now) or utcnow()
    settings = lease_settings()
    clean_note = validate_ledger_text(note.strip(), "note")
    old_status = task.status
    old_holder = task.holder
    old_state = task_row_state(task)
    new_status = old_status
    if old_status == "blocked":
        validate_transition(old_status, "doing")
        new_status = "doing"
        task.blocked_reason = None
    elif old_status in {"queued", "handoff"}:
        validate_transition(old_status, "doing")
        new_status = "doing"
    granted = _activate_lease(
        task, holder=task.holder, now=clock, settings=settings, increment=True
    )
    task.retry_count = 0
    task.failure_class = None
    task.status = new_status
    task.open_dispatch = False
    if workdir_key or task.workdir_key:
        acquire_workdir_lock(db, task, workdir_key or task.workdir_key, clock)
    new_state = {
        **old_state,
        "status": new_status,
        "blocked_reason": None,
    }
    granted["action"] = "retry"
    granted["retry_count"] = 0
    _append_lease_event(
        task,
        who=who,
        note=clean_note,
        old_status=old_status,
        new_status=new_status,
        old_holder=old_holder,
        new_holder=old_holder,
        old_state=old_state,
        new_state=new_state,
        lease=granted,
    )
    db.flush()
    return task


def apply_reported_failure(
    db: Session,
    task: Task,
    *,
    who: str,
    failure_class: str,
    reason: str,
    lease_term: int | None,
    is_privileged: bool,
    now: dt.datetime | None = None,
) -> dict[str, Any]:
    """Apply the two-layer retry policy after an attempt has been recorded."""
    clock = _aware(now) or utcnow()
    if not is_privileged:
        assert_lease_write(task, lease_term, is_privileged=False, now=clock)
    kind = failure_class
    if kind in LEASE_FAILURE_SEMANTIC or kind == "semantic":
        escalate_task(
            db,
            task,
            who=who,
            note="semantic failure; waiting for a human to fix the cause",
            reason=reason,
            failure_class="semantic",
            lease_term=lease_term,
            is_privileged=is_privileged,
            now=clock,
        )
        return {"action": "escalate", "failure_class": "semantic"}
    if kind not in LEASE_FAILURE_TRANSIENT and kind != "transient" and kind != "precheck":
        raise ProtocolError(f"unknown failure class: {failure_class!r}")
    settings = lease_settings()
    next_count = int(task.retry_count or 0) + 1
    if next_count > settings.retry_limit:
        escalate_task(
            db,
            task,
            who=who,
            note="retry limit reached; escalated for a human",
            reason=reason,
            failure_class="transient" if kind != "precheck" else "precheck",
            lease_term=lease_term,
            is_privileged=is_privileged,
            now=clock,
        )
        return {"action": "escalate", "failure_class": kind, "retry_count": next_count}
    task.retry_count = next_count
    task.failure_class = "transient" if kind != "precheck" else "precheck"
    old_state = task_row_state(task)
    _append_lease_event(
        task,
        who=who,
        note=f"transient failure recorded; retry {next_count} of {settings.retry_limit}",
        old_status=task.status,
        new_status=task.status,
        old_holder=task.holder,
        new_holder=task.holder,
        old_state=old_state,
        new_state=old_state,
        lease={
            "action": "retry",
            "term": int(task.lease_term or 0),
            "reason": validate_ledger_text(reason, "reason"),
            "failure_class": task.failure_class,
            "retry_count": next_count,
        },
    )
    db.flush()
    return {
        "action": "retry",
        "failure_class": task.failure_class,
        "retry_count": next_count,
        "plan": retry_plan(task, task.attempts[-1] if task.attempts else None),
    }


def precheck_deliverable(
    db: Session,
    task: Task,
    *,
    who: str,
    lease_term: int,
    checks: Iterable[dict[str, Any]],
    is_privileged: bool = False,
    now: dt.datetime | None = None,
) -> dict[str, Any]:
    """Compare submitted checks against the card's acceptance list."""
    if not is_privileged and task.holder != who:
        raise Forbidden(f"holder-only-writes: {who!r} does not hold {task.id}")
    clock = _aware(now) or utcnow()
    if not is_privileged:
        assert_lease_write(task, lease_term, is_privileged=False, now=clock)
    items = list(checks)
    by_item = {}
    for row in items:
        item = str(row.get("item") or "").strip()
        if not item:
            raise ProtocolError("precheck item must be non-empty")
        validate_ledger_text(item, "precheck item")
        feedback = str(row.get("feedback") or "").strip()
        if feedback:
            validate_ledger_text(feedback, "precheck feedback")
        by_item[item] = {
            "item": item,
            "passed": bool(row.get("passed")),
            "feedback": feedback,
        }
    missing = [item for item in task.acceptance if item not in by_item]
    failed = [
        row for row in by_item.values() if not row["passed"]
    ]
    passed = not missing and not failed
    if passed:
        return {
            "passed": True,
            "missing": [],
            "failed": [],
            "action": "pass",
        }
    reasons = []
    if missing:
        reasons.append(f"missing {len(missing)} acceptance checks")
    if failed:
        reasons.append(f"{len(failed)} checks failed")
    reason = "; ".join(reasons)
    policy = apply_reported_failure(
        db,
        task,
        who=who,
        failure_class="precheck",
        reason=reason,
        lease_term=lease_term,
        is_privileged=is_privileged,
        now=clock,
    )
    return {
        "passed": False,
        "missing": missing,
        "failed": failed,
        **policy,
    }


def build_start_briefing(
    db: Session, task: Task, claimant: str
) -> dict[str, Any]:
    """CrewAI-shaped kickoff packet: similar cards, sessions, no bodies."""
    similar_query = (
        select(Task)
        .options(raiseload(Task.events), raiseload(Task.attempts))
        .where(Task.id != task.id)
        .order_by(Task.updated_at.desc(), Task.id.desc())
        .limit(8)
    )
    if task.dept:
        similar_query = similar_query.where(Task.dept == task.dept)
    similar = [
        {
            "id": row.id,
            "title": row.title,
            "status": row.status,
            "dept": row.dept,
        }
        for row in db.execute(similar_query).scalars()
    ][:5]
    sessions = [
        {
            "id": row.id,
            "title": row.title,
            "runtime": row.runtime,
            "task_id": row.task_id,
            "resume_capable": row.resume_capable,
        }
        for row in db.execute(
            select(RuntimeSession)
            .where(
                or_(
                    RuntimeSession.task_id == task.id,
                    RuntimeSession.actor_id == claimant,
                )
            )
            .order_by(RuntimeSession.synced_at.desc())
            .limit(5)
        ).scalars()
    ]
    return {
        "similar_tasks": similar,
        "related_sessions": sessions,
    }
