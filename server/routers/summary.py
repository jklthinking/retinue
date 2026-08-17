"""First-screen summary read model.

One authenticated GET returns everything the workbench home page needs to
paint: the five action-queue lanes (count plus the first few rows each), the
kanban column counts, the actor roster, pending approvals, and the most
recent chain events. ``updated_since`` turns the task section into an
incremental delta (only rows changed after the watermark), so polling stays
cheap as the task table grows; the aggregate sections are always returned in
full because they are bounded and cheap to compute.

This router is strictly read-only: it never writes, and access follows the
same login-session rule as every other read endpoint (``require_auth``).
Response models live in this module on purpose — ``server/schemas.py`` holds
request bodies and is owned by another workstream.
"""

from __future__ import annotations

import datetime as dt
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.orm import Session, raiseload

from ..db import Actor, Approval, Task, TaskEvent, utcnow
from ..deps import ONLINE_WINDOW, Principal, get_db, require_auth
from ..engine import dependency_graph, task_summary_to_dict
from ..flow import approval_to_dict
from ..helpers import actor_to_dict

router = APIRouter()

# Lane predicates mirror the client-side definitions that used to filter the
# full task list in the browser; keep them in sync with webui ActionQueue.
ACTIVE_STATUSES = ("queued", "doing", "handoff", "blocked")
IN_FLIGHT_STATUSES = ("doing", "handoff")
RECENT_EVENTS_LIMIT = 9
PENDING_APPROVALS_LIMIT = 100


class LaneSummary(BaseModel):
    """One action-queue lane: total count plus the first rows."""

    count: int
    items: list[dict[str, Any]]


class LostExecutorLane(BaseModel):
    """The lost-executor lane pairs each in-flight card with its offline agent."""

    count: int
    items: list[dict[str, Any]]  # {"task": TaskSummary, "actor": ActorInfo}


class SummaryLanes(BaseModel):
    decisions: LaneSummary
    due_today: LaneSummary
    overdue: LaneSummary
    blocked: LaneSummary
    lost_executors: LostExecutorLane


class SummaryResponse(BaseModel):
    generated_at: str  # watermark the client echoes back as updated_since
    today: str  # the calendar date the due lanes were computed against
    partial: bool  # True when tasks is an updated_since delta, not a full list
    task_counts: dict[str, int]
    lanes: SummaryLanes
    approvals: list[dict[str, Any]]
    actors: list[dict[str, Any]]
    tasks: list[dict[str, Any]] | None  # None when include_tasks=false
    recent_events: list[dict[str, Any]]


def _parse_updated_since(raw: str | None) -> dt.datetime | None:
    if raw is None:
        return None
    try:
        parsed = dt.datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError as exc:
        raise HTTPException(status_code=422, detail="invalid updated_since timestamp") from exc
    # Task.updated_at is stored naive (UTC); compare on the same basis.
    return parsed.replace(tzinfo=None)


def _parse_today(raw: str | None) -> dt.date:
    if raw is None:
        return dt.date.today()
    try:
        return dt.date.fromisoformat(raw)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail="invalid today date") from exc


def _summary_query():
    """Task rows without their event chains or attempts (summary fields only)."""
    return (
        select(Task)
        .options(raiseload(Task.events), raiseload(Task.attempts))
        .order_by(Task.created_at.desc(), Task.id.desc())
    )


def _recent_events(db: Session) -> list[dict[str, Any]]:
    rows = db.execute(
        select(TaskEvent, Task.title)
        .join(Task, Task.id == TaskEvent.task_id)
        .where(Task.archived.is_(False))
        .order_by(TaskEvent.at.desc(), TaskEvent.id.desc())
        .limit(RECENT_EVENTS_LIMIT)
    ).all()
    return [
        {
            "who": event.who,
            "did": event.did,
            "at": event.at,
            "from_status": event.from_status,
            "to_status": event.to_status,
            "task_id": event.task_id,
            "task_title": title,
        }
        for event, title in rows
    ]


@router.get("/api/summary", response_model=SummaryResponse)
def get_summary(
    updated_since: str | None = None,
    today: str | None = None,
    lane_limit: int = Query(default=5, ge=1, le=50),
    include_tasks: bool = True,
    include_archived: bool = False,
    principal: Principal = Depends(require_auth),
    db: Session = Depends(get_db, scope="function"),
) -> SummaryResponse:
    generated_at = utcnow()
    since = _parse_updated_since(updated_since)
    today_date = _parse_today(today)
    online_cutoff = utcnow() - ONLINE_WINDOW
    offline_cutoff = online_cutoff.replace(tzinfo=None)

    # ----- lanes: always computed in full (aggregates are cheap, payloads
    # bounded), even for incremental polls, so the first screen never waits
    # on a full table transfer.
    lane_tasks = list(
        db.execute(_summary_query().where(Task.archived.is_(False))).scalars()
    )

    section_tasks: list[Task] | None = None
    if include_tasks:
        if since is None:
            if include_archived:
                section_tasks = list(db.execute(_summary_query()).scalars())
            else:
                section_tasks = lane_tasks
        else:
            query = (
                _summary_query()
                .where(Task.updated_at > since)
                .order_by(Task.updated_at.desc(), Task.id.desc())
            )
            if not include_archived:
                query = query.where(Task.archived.is_(False))
            section_tasks = list(db.execute(query).scalars())

    ids = {task.id for task in lane_tasks}
    if section_tasks:
        ids.update(task.id for task in section_tasks)
    graph = dependency_graph(db, ids)
    projections = {
        task.id: task_summary_to_dict(task, graph[task.id])
        for task in lane_tasks + list(section_tasks or [])
    }

    def lane(matching: list[Task]) -> LaneSummary:
        return LaneSummary(
            count=len(matching),
            items=[projections[task.id] for task in matching[:lane_limit]],
        )

    due_today = [
        task
        for task in lane_tasks
        if task.due_at == today_date and task.status in ACTIVE_STATUSES
    ]
    overdue = [
        task
        for task in lane_tasks
        if task.due_at is not None
        and task.due_at < today_date
        and task.status in ACTIVE_STATUSES
    ]
    blocked = [task for task in lane_tasks if task.status == "blocked"]

    actors = list(db.execute(select(Actor).order_by(Actor.kind, Actor.id)).scalars())
    actors_by_id = {actor.id: actor for actor in actors}
    lost_rows = []
    for task in lane_tasks:
        if task.status not in IN_FLIGHT_STATUSES:
            continue
        actor = actors_by_id.get(task.holder)
        if actor is None or actor.kind != "agent" or actor.disabled:
            continue
        last_seen = actor.last_seen_at
        if last_seen is not None and last_seen > offline_cutoff:
            continue
        lost_rows.append(
            {
                "task": projections[task.id],
                "actor": actor_to_dict(actor, online_cutoff),
            }
        )

    pending = list(
        db.execute(
            select(Approval)
            .where(Approval.status == "pending")
            .order_by(Approval.created_at.desc())
            .limit(PENDING_APPROVALS_LIMIT)
        ).scalars()
    )
    approvals = [approval_to_dict(a, db.get(Task, a.task_id)) for a in pending]

    counts = dict(
        db.execute(
            select(Task.status, func.count()).where(Task.archived.is_(False)).group_by(Task.status)
        ).all()
    )

    return SummaryResponse(
        generated_at=generated_at.isoformat(),
        today=today_date.isoformat(),
        partial=since is not None,
        task_counts=counts,
        lanes=SummaryLanes(
            decisions=LaneSummary(
                count=len(approvals), items=approvals[:lane_limit]
            ),
            due_today=lane(due_today),
            overdue=lane(overdue),
            blocked=lane(blocked),
            lost_executors=LostExecutorLane(
                count=len(lost_rows), items=lost_rows[:lane_limit]
            ),
        ),
        approvals=approvals,
        actors=[actor_to_dict(actor, online_cutoff) for actor in actors],
        tasks=(
            None
            if section_tasks is None
            else [projections[task.id] for task in section_tasks]
        ),
        recent_events=_recent_events(db),
    )
