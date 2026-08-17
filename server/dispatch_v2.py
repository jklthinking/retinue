"""Board-comment mentions, calendar/alert/callback dispatch, and squad routing.

These sit on the existing orchestration sweep (the reclaim endpoint) and the
append-only task chain. They do not invent a second state machine.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import re
from dataclasses import dataclass
from typing import Any, Iterable

from sqlalchemy import select
from sqlalchemy.orm import Session

from core.protocol.task import PRIORITIES, ProtocolError

from .db import (
    Actor,
    DispatchSchedule,
    DispatchTrigger,
    Squad,
    SquadMember,
    Task,
    utcnow,
)
from .engine import (
    ORCHESTRATION_ACTOR,
    Conflict,
    Forbidden,
    append_annotation_event,
    create_task,
    update_task,
)
from .matching import match_agents


MENTION_RE = re.compile(
    r"@([A-Za-z0-9][A-Za-z0-9._-]{0,63}|[\u4e00-\u9fff]{1,32})"
)
SQUAD_ID_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
TRIGGER_SOURCES = ("alert", "callback", "schedule")


@dataclass(frozen=True)
class MentionHit:
    token: str
    actor_id: str
    kind: str


def _aware(value: dt.datetime | None) -> dt.datetime | None:
    if value is None:
        return None
    if value.tzinfo is None or value.utcoffset() is None:
        return value.replace(tzinfo=dt.timezone.utc)
    return value.astimezone(dt.timezone.utc)


def _event_key(prefix: str, *parts: str) -> str:
    digest = hashlib.sha256("\0".join(parts).encode()).hexdigest()[:24]
    return f"{prefix}-{digest}"


def parse_mention_tokens(text: str) -> list[str]:
    """Return unique @tokens in document order."""
    seen: set[str] = set()
    ordered: list[str] = []
    for match in MENTION_RE.finditer(text or ""):
        token = match.group(1)
        key = token.casefold()
        if key in seen:
            continue
        seen.add(key)
        ordered.append(token)
    return ordered


def resolve_mentions(db: Session, text: str) -> list[MentionHit]:
    """Map @tokens onto enabled roster actors; unknown tokens are dropped."""
    tokens = parse_mention_tokens(text)
    if not tokens:
        return []
    actors = list(
        db.execute(select(Actor).where(Actor.disabled.is_(False))).scalars()
    )
    by_id = {actor.id.casefold(): actor for actor in actors}
    by_name: dict[str, Actor] = {}
    for actor in actors:
        name = (actor.display_name or "").strip().casefold()
        if name and name not in by_name:
            by_name[name] = actor
    hits: list[MentionHit] = []
    seen_ids: set[str] = set()
    for token in tokens:
        actor = by_id.get(token.casefold()) or by_name.get(token.casefold())
        if actor is None or actor.id in seen_ids:
            continue
        seen_ids.add(actor.id)
        hits.append(MentionHit(token=token, actor_id=actor.id, kind=actor.kind))
    return hits


def apply_mentions(
    db: Session,
    task: Task,
    *,
    who: str,
    text: str,
    source_type: str,
    source_key: str,
    is_privileged: bool,
) -> dict[str, Any] | None:
    """Record a mention trigger and the resulting invite, reassignment, or notice."""
    hits = resolve_mentions(db, text)
    if not hits:
        return None
    trigger_key = _event_key("mention", task.id, source_key, "trigger")
    existing = next(
        (event for event in task.events if event.event_key == trigger_key),
        None,
    )
    if existing is not None:
        return None
    mentioned = [
        {"token": hit.token, "actor_id": hit.actor_id, "kind": hit.kind}
        for hit in hits
    ]
    append_annotation_event(
        db,
        task,
        who=who,
        did="提及触发：" + "、".join(f"@{hit.actor_id}" for hit in hits),
        event_type="mention_trigger",
        event_key=trigger_key,
        parent_key=source_key if source_key.startswith("review-") else None,
        payload={
            "source_type": source_type,
            "source_key": source_key,
            "mentions": mentioned,
        },
    )
    first_agent = next((hit for hit in hits if hit.kind == "agent"), None)
    action = "notified"
    assigned = None
    if task.status in ("done", "cancelled"):
        action = "notified"
    elif task.open_dispatch and first_agent is not None:
        task.next_holder = first_agent.actor_id
        action = "invited"
        assigned = first_agent.actor_id
    elif (
        first_agent is not None
        and task.status == "queued"
        and (is_privileged or task.holder == who)
        and first_agent.actor_id != task.holder
    ):
        update_task(
            db,
            task,
            who=who,
            is_privileged=is_privileged,
            holder=first_agent.actor_id,
            note=f"mention reassigned the baton to {first_agent.actor_id}",
        )
        action = "reassigned"
        assigned = first_agent.actor_id
    if action == "invited":
        did = f"提及结果：邀请 {assigned} 接单"
    elif action == "reassigned":
        did = f"提及结果：已转派给 {assigned}"
    else:
        names = "、".join(hit.actor_id for hit in hits)
        did = f"提及结果：已通知 {names} 回应"
    append_annotation_event(
        db,
        task,
        who=who,
        did=did,
        event_type="mention_result",
        event_key=_event_key("mresult", task.id, source_key, action),
        parent_key=trigger_key,
        payload={
            "action": action,
            "assigned": assigned,
            "mentions": mentioned,
        },
    )
    db.flush()
    return {"action": action, "assigned": assigned, "mentions": mentioned}


def _require_squad(db: Session, squad_id: str) -> Squad:
    squad = db.get(Squad, squad_id)
    if squad is None:
        raise ProtocolError(f"unknown squad: {squad_id}")
    return squad


def squad_member_ids(db: Session, squad_id: str) -> list[str]:
    return list(
        db.execute(
            select(SquadMember.actor_id)
            .where(SquadMember.squad_id == squad_id)
            .order_by(SquadMember.actor_id)
        ).scalars()
    )


def squad_to_dict(db: Session, squad: Squad) -> dict[str, Any]:
    return {
        "id": squad.id,
        "display_name": squad.display_name,
        "leader_id": squad.leader_id,
        "created_by": squad.created_by,
        "members": squad_member_ids(db, squad.id),
        "created_at": squad.created_at.isoformat() if squad.created_at else None,
    }


def create_squad(
    db: Session,
    *,
    squad_id: str,
    display_name: str,
    leader_id: str,
    created_by: str,
    members: Iterable[str] = (),
) -> Squad:
    if not SQUAD_ID_RE.fullmatch(squad_id):
        raise ProtocolError("squad id must be a lowercase slug")
    if db.get(Squad, squad_id) is not None:
        raise Conflict(f"squad already exists: {squad_id}")
    leader = db.get(Actor, leader_id)
    if leader is None or leader.disabled:
        raise ProtocolError(f"leader: unknown or disabled actor {leader_id!r}")
    name = (display_name or "").strip() or squad_id
    if len(name) > 128:
        raise ProtocolError("display_name must not exceed 128 characters")
    squad = Squad(
        id=squad_id,
        display_name=name,
        leader_id=leader.id,
        created_by=created_by,
    )
    db.add(squad)
    db.flush()
    wanted = list(dict.fromkeys([leader.id, *[item.strip() for item in members if item]]))
    for actor_id in wanted:
        add_squad_member(db, squad, actor_id=actor_id)
    return squad


def add_squad_member(db: Session, squad: Squad, *, actor_id: str) -> SquadMember:
    actor = db.get(Actor, actor_id)
    if actor is None or actor.disabled:
        raise ProtocolError(f"member: unknown or disabled actor {actor_id!r}")
    existing = db.execute(
        select(SquadMember).where(
            SquadMember.squad_id == squad.id,
            SquadMember.actor_id == actor.id,
        )
    ).scalar()
    if existing is not None:
        return existing
    row = SquadMember(squad_id=squad.id, actor_id=actor.id)
    db.add(row)
    db.flush()
    return row


def assign_task_squad(
    db: Session,
    task: Task,
    *,
    squad_id: str,
    who: str,
) -> Task:
    if task.status in ("done", "cancelled"):
        raise ProtocolError(f"{task.status} is terminal; {task.id} can no longer be mutated")
    if not task.open_dispatch:
        raise ProtocolError("only an open-dispatch card can be addressed to a squad")
    squad = _require_squad(db, squad_id)
    task.squad_id = squad.id
    append_annotation_event(
        db,
        task,
        who=who,
        did=f"指给编队 {squad.id}，由领队 {squad.leader_id} 路由",
        event_type="squad_assign",
        event_key=_event_key("squadas", task.id, squad.id),
        payload={"squad_id": squad.id, "leader_id": squad.leader_id},
    )
    db.flush()
    return task


def _context_query(task: Task) -> str:
    parts = [task.title, *(task.acceptance or [])]
    return " ".join(part.strip() for part in parts if part and part.strip())


def _pick_squad_member(
    db: Session, task: Task, member_ids: list[str], *, member_id: str | None
) -> tuple[str, str]:
    if member_id:
        if member_id not in member_ids:
            raise ProtocolError(f"{member_id} is not a member of squad {task.squad_id}")
        actor = db.get(Actor, member_id)
        if actor is None or actor.disabled:
            raise ProtocolError(f"member: unknown or disabled actor {member_id!r}")
        return member_id, f"领队指定成员 {member_id}"
    if not member_ids:
        raise ProtocolError(f"squad {task.squad_id} has no members to route to")
    ranked = match_agents(db, _context_query(task), limit=20)
    by_id = {row["id"]: row for row in ranked}
    ordered = [by_id[actor_id] for actor_id in member_ids if actor_id in by_id]
    if not ordered:
        # Members that are humans (or otherwise absent from agent-match) stay
        # selectable in stable id order so a formation is never silently stuck.
        fallback = sorted(member_ids)
        return fallback[0], f"编队内无匹配代理，按标识选择 {fallback[0]}"
    ordered.sort(
        key=lambda row: (row["score"], row["online"], -row["active_tasks"], row["id"]),
        reverse=True,
    )
    winner = ordered[0]
    reason = winner["reasons"][0] if winner["reasons"] else "技能画像最高分"
    return winner["id"], f"{reason}（分 {winner['score']}）"


def route_squad_task(
    db: Session,
    task: Task,
    *,
    who: str,
    is_privileged: bool,
    member_id: str | None = None,
) -> dict[str, Any]:
    """Leader (or privileged operator) places an open squad card with one member."""
    if not task.squad_id:
        raise ProtocolError(f"{task.id} is not addressed to a squad")
    if not task.open_dispatch or task.status not in {"queued", "blocked"}:
        raise Conflict(f"task is no longer routable: {task.id}")
    squad = _require_squad(db, task.squad_id)
    if not is_privileged and who != squad.leader_id:
        raise Forbidden(f"only leader {squad.leader_id} may route squad {squad.id}")
    member_ids = squad_member_ids(db, squad.id)
    chosen, reason = _pick_squad_member(
        db, task, member_ids, member_id=member_id
    )
    sentence = f"领队路由：派给 {chosen}，依据：{reason}"
    if chosen != task.holder:
        update_task(
            db,
            task,
            who=who,
            is_privileged=True,
            holder=chosen,
            note=sentence,
        )
    else:
        append_annotation_event(
            db,
            task,
            who=who,
            did=sentence,
            event_type="squad_route",
            event_key=_event_key("squadrt", task.id, chosen, reason),
            payload={
                "squad_id": squad.id,
                "chosen": chosen,
                "reason": reason,
            },
        )
    if task.events and task.events[-1].event_type != "squad_route":
        append_annotation_event(
            db,
            task,
            who=who,
            did=sentence,
            event_type="squad_route",
            event_key=_event_key("squadrt", task.id, chosen, reason),
            payload={
                "squad_id": squad.id,
                "chosen": chosen,
                "reason": reason,
            },
        )
    db.flush()
    return {
        "task_id": task.id,
        "squad_id": squad.id,
        "chosen": chosen,
        "reason": reason,
    }


def route_open_squad_cards(
    db: Session, *, now: dt.datetime | None = None
) -> list[dict[str, Any]]:
    """Sweep hall cards addressed to a squad and let the leader matcher place them."""
    del now
    cards = list(
        db.execute(
            select(Task).where(
                Task.open_dispatch.is_(True),
                Task.squad_id.is_not(None),
                Task.status.in_(("queued", "blocked")),
            )
        ).scalars()
    )
    results: list[dict[str, Any]] = []
    for task in cards:
        members = squad_member_ids(db, task.squad_id or "")
        if not members:
            continue
        try:
            routed = route_squad_task(
                db,
                task,
                who=ORCHESTRATION_ACTOR,
                is_privileged=True,
            )
        except (ProtocolError, Conflict):
            continue
        results.append({"task_id": task.id, "action": "squad-route", **routed})
    return results


def _parse_fire_at(value: str | dt.datetime) -> dt.datetime:
    if isinstance(value, dt.datetime):
        aware = _aware(value)
        if aware is None:
            raise ProtocolError("fire_at must include a timezone")
        return aware
    text = value.strip()
    if not text:
        raise ProtocolError("fire_at is required")
    try:
        parsed = dt.datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ProtocolError("fire_at must be an ISO-8601 timestamp") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ProtocolError("fire_at must include a timezone")
    return parsed.astimezone(dt.timezone.utc)


def create_dispatch_schedule(
    db: Session,
    *,
    schedule_key: str,
    title: str,
    fire_at: str | dt.datetime,
    created_by: str,
    holder: str | None = None,
    open_dispatch: bool = True,
    squad_id: str | None = None,
    dept: str | None = None,
    priority: str = "none",
    acceptance: Iterable[str] = (),
    note: str = "",
    repeat_seconds: int | None = None,
) -> DispatchSchedule:
    key = schedule_key.strip()
    if not key or len(key) > 128:
        raise ProtocolError("schedule_key must be 1 to 128 characters")
    existing = db.execute(
        select(DispatchSchedule).where(DispatchSchedule.schedule_key == key)
    ).scalar()
    if existing is not None:
        raise Conflict(f"schedule already exists: {key}")
    if not title or not title.strip():
        raise ProtocolError("title must be non-empty")
    if priority not in PRIORITIES:
        raise ProtocolError(f"priority must be one of: {', '.join(PRIORITIES)}")
    if repeat_seconds is not None and repeat_seconds < 60:
        raise ProtocolError("repeat_seconds must be at least 60 when set")
    if squad_id:
        _require_squad(db, squad_id)
    if open_dispatch:
        assigned = holder or created_by
        if assigned != created_by:
            raise ProtocolError("an open-dispatch schedule cannot also assign an executor")
        holder_value = None
    else:
        if not holder:
            raise ProtocolError("holder is required unless open_dispatch is set")
        actor = db.get(Actor, holder)
        if actor is None or actor.disabled:
            raise ProtocolError(f"holder: unknown or disabled actor {holder!r}")
        holder_value = holder
    row = DispatchSchedule(
        schedule_key=key,
        title=title.strip()[:256],
        fire_at=_parse_fire_at(fire_at),
        created_by=created_by,
        holder=holder_value,
        open_dispatch=open_dispatch,
        squad_id=squad_id,
        dept=(dept.strip() if dept and dept.strip() else None),
        priority=priority,
        acceptance_json=json.dumps(
            [item.strip() for item in acceptance if item and item.strip()],
            ensure_ascii=False,
        ),
        note=(note or "").strip()[:240],
        repeat_seconds=repeat_seconds,
        status="pending",
    )
    db.add(row)
    db.flush()
    return row


def schedule_to_dict(row: DispatchSchedule) -> dict[str, Any]:
    return {
        "id": row.id,
        "schedule_key": row.schedule_key,
        "title": row.title,
        "fire_at": row.fire_at.isoformat() if row.fire_at else None,
        "created_by": row.created_by,
        "holder": row.holder,
        "open_dispatch": row.open_dispatch,
        "squad_id": row.squad_id,
        "dept": row.dept,
        "priority": row.priority,
        "acceptance": json.loads(row.acceptance_json or "[]"),
        "note": row.note,
        "repeat_seconds": row.repeat_seconds,
        "status": row.status,
        "last_fired_at": row.last_fired_at.isoformat() if row.last_fired_at else None,
        "last_task_id": row.last_task_id,
    }


def _trigger_hash(
    *,
    source: str,
    title: str,
    holder: str | None,
    open_dispatch: bool,
    squad_id: str | None,
    priority: str,
    acceptance: list[str],
    note: str,
) -> str:
    payload = {
        "source": source,
        "title": title,
        "holder": holder,
        "open_dispatch": open_dispatch,
        "squad_id": squad_id,
        "priority": priority,
        "acceptance": acceptance,
        "note": note,
    }
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode()).hexdigest()


def _open_from_trigger(
    db: Session,
    *,
    source: str,
    idempotency_key: str,
    created_by: str,
    title: str,
    holder: str | None,
    open_dispatch: bool,
    squad_id: str | None,
    dept: str | None,
    priority: str,
    acceptance: list[str],
    note: str,
) -> tuple[Task, bool]:
    if source not in TRIGGER_SOURCES:
        raise ProtocolError(f"source must be one of: {', '.join(TRIGGER_SOURCES)}")
    if not idempotency_key or len(idempotency_key) > 128:
        raise ProtocolError("idempotency_key must be 1 to 128 characters")
    request_hash = _trigger_hash(
        source=source,
        title=title,
        holder=holder,
        open_dispatch=open_dispatch,
        squad_id=squad_id,
        priority=priority,
        acceptance=acceptance,
        note=note,
    )
    existing = db.execute(
        select(DispatchTrigger).where(
            DispatchTrigger.source == source,
            DispatchTrigger.idempotency_key == idempotency_key,
        )
    ).scalar()
    if existing is not None:
        if existing.request_hash != request_hash:
            raise Conflict(
                "idempotency key was already used for a different dispatch event"
            )
        task = db.get(Task, existing.task_id)
        if task is None:
            raise ProtocolError("idempotency record points to a missing card")
        return task, False
    if squad_id:
        _require_squad(db, squad_id)
    publisher = created_by
    if open_dispatch:
        assigned = publisher
    else:
        assigned = holder or publisher
    card_note = note or f"opened by {source}"
    task = create_task(
        db,
        title=title,
        created_by=publisher,
        holder=assigned,
        dept=dept,
        priority=priority,
        acceptance=acceptance,
        note=card_note,
        open_dispatch=open_dispatch,
        event_payload={
            "dispatch_trigger": {
                "source": source,
                "idempotency_key": idempotency_key,
            }
        },
        squad_id=squad_id,
    )
    db.add(
        DispatchTrigger(
            source=source,
            idempotency_key=idempotency_key,
            request_hash=request_hash,
            task_id=task.id,
        )
    )
    db.flush()
    return task, True


def ingest_dispatch_event(
    db: Session,
    *,
    actor_id: str,
    source: str,
    idempotency_key: str,
    title: str,
    holder: str | None = None,
    open_dispatch: bool = True,
    squad_id: str | None = None,
    dept: str | None = None,
    priority: str = "none",
    acceptance: Iterable[str] = (),
    note: str = "",
) -> tuple[Task, bool]:
    """Create one card per (source, idempotency_key) for an alert or callback."""
    if source not in ("alert", "callback"):
        raise ProtocolError("inbound source must be alert or callback")
    criteria = [item.strip() for item in acceptance if item and item.strip()]
    return _open_from_trigger(
        db,
        source=source,
        idempotency_key=idempotency_key,
        created_by=actor_id,
        title=title,
        holder=holder,
        open_dispatch=open_dispatch,
        squad_id=squad_id,
        dept=dept,
        priority=priority,
        acceptance=criteria,
        note=(note or "").strip(),
    )


def fire_due_schedules(
    db: Session, *, now: dt.datetime | None = None
) -> list[dict[str, Any]]:
    """Open due calendar rows. Safe to call from the existing reclaim sweep."""
    clock = _aware(now) or utcnow()
    pending = list(
        db.execute(
            select(DispatchSchedule)
            .where(DispatchSchedule.status == "pending")
            .order_by(DispatchSchedule.id)
        ).scalars()
    )
    due = [
        row
        for row in pending
        if (_aware(row.fire_at) or clock) <= clock
    ]
    results: list[dict[str, Any]] = []
    for row in due:
        fire_stamp = _aware(row.fire_at) or clock
        fire_key = f"{row.schedule_key}:{fire_stamp.strftime('%Y%m%dT%H%M%SZ')}"
        acceptance = json.loads(row.acceptance_json or "[]")
        task, created = _open_from_trigger(
            db,
            source="schedule",
            idempotency_key=fire_key,
            created_by=row.created_by,
            title=row.title,
            holder=row.holder,
            open_dispatch=row.open_dispatch,
            squad_id=row.squad_id,
            dept=row.dept,
            priority=row.priority,
            acceptance=list(acceptance) if isinstance(acceptance, list) else [],
            note=row.note or f"opened by schedule {row.schedule_key}",
        )
        row.last_fired_at = clock
        row.last_task_id = task.id
        if row.repeat_seconds:
            nxt = fire_stamp
            step = dt.timedelta(seconds=int(row.repeat_seconds))
            while nxt <= clock:
                nxt = nxt + step
            row.fire_at = nxt
            row.status = "pending"
        else:
            row.status = "fired"
        results.append(
            {
                "task_id": task.id,
                "action": "schedule-fire",
                "schedule_key": row.schedule_key,
                "created": created,
            }
        )
    db.flush()
    return results


def run_dispatch_sweeps(
    db: Session, *, now: dt.datetime | None = None
) -> dict[str, Any]:
    """Lease reclaim plus due schedules and squad leader routing."""
    from .engine import reclaim_expired_leases

    from .pipeline_v2 import resume_open_instances

    clock = _aware(now) or utcnow()
    resumed = resume_open_instances(db, now=clock)
    reclaimed = reclaim_expired_leases(db, now=clock)
    fired = fire_due_schedules(db, now=clock)
    routed = route_open_squad_cards(db, now=clock)
    return {
        "reclaimed": reclaimed,
        "count": len(reclaimed),
        "fired": fired,
        "routed": routed,
        "resumed": resumed,
    }
