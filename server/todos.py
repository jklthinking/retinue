"""Private personal todos: ownership, proposals, reminders, and task links.

A TodoItem is not a shared board card. The owner user is the only default
reader and writer. Viewers are refused on every route. Administrators do not
inherit read access; a compliance read must carry an explicit reason and
appends an audit event. Agents may submit TodoProposal rows only when that
owner has granted them ``todo:propose``, and they cannot read another
person's confirmed items.
"""

from __future__ import annotations

import datetime as dt
import json
from typing import Any, Iterable

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from core.protocol.task import ProtocolError

from .db import (
    Actor,
    ReminderDelivery,
    Task,
    TodoEvent,
    TodoItem,
    TodoProposal,
    TodoTaskLink,
    User,
    utcnow,
)
from .deps import Principal
from .engine import Forbidden, _allocate_sequence, create_task, parse_due_date


PROPOSAL_PENDING = "pending"
PROPOSAL_CONFIRMED = "confirmed"
PROPOSAL_REJECTED = "rejected"

ITEM_OPEN = "open"
ITEM_DONE = "done"
ITEM_CANCELLED = "cancelled"
ITEM_SNOOZED = "snoozed"
ITEM_PROMOTED = "promoted"

ACTIVE_ITEM_STATUSES = (ITEM_OPEN, ITEM_SNOOZED)
TERMINAL_TASK_STATUSES = {"done", "cancelled"}

CAPABILITY_PROPOSE = "todo:propose"
REMINDER_CHANNEL_DEFAULT = "pending"
MIN_ACCESS_REASON = 8


def _iso(value: dt.datetime | dt.date | None) -> str | None:
    if value is None:
        return None
    if isinstance(value, dt.datetime):
        if value.tzinfo is None:
            value = value.replace(tzinfo=dt.timezone.utc)
        else:
            value = value.astimezone(dt.timezone.utc)
        return value.isoformat().replace("+00:00", "Z")
    return value.isoformat()


def parse_remind_at(value: str | dt.datetime | None) -> dt.datetime | None:
    if value is None:
        return None
    if isinstance(value, dt.datetime):
        if value.tzinfo is None:
            return value.replace(tzinfo=dt.timezone.utc)
        return value.astimezone(dt.timezone.utc)
    text = value.strip()
    if not text:
        return None
    try:
        parsed = dt.datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ProtocolError("remind_at must be an ISO-8601 datetime") from exc
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=dt.timezone.utc)
    return parsed.astimezone(dt.timezone.utc)


def _today() -> dt.date:
    return utcnow().date()


def _actor_name(principal: Principal) -> str:
    return principal.write_identity


def _who_kind(principal: Principal) -> str:
    if principal.kind == "agent":
        return "agent"
    if principal.role == "admin":
        return "admin"
    return "user"


def grants_of(user: User) -> list[str]:
    try:
        raw = json.loads(user.todo_propose_grants_json or "[]")
    except json.JSONDecodeError:
        return []
    if not isinstance(raw, list):
        return []
    return [str(item) for item in raw if isinstance(item, str) and item.strip()]


def _set_grants(user: User, actor_ids: Iterable[str]) -> list[str]:
    cleaned = list(dict.fromkeys(actor.strip() for actor in actor_ids if actor and actor.strip()))
    user.todo_propose_grants_json = json.dumps(cleaned, ensure_ascii=False)
    return cleaned


def assert_not_viewer(principal: Principal) -> None:
    if principal.role == "viewer":
        raise Forbidden("viewers cannot read private todos")


def require_owner_user(db: Session, principal: Principal) -> User:
    assert_not_viewer(principal)
    if principal.kind != "user" or principal.user is None:
        raise Forbidden("only a signed-in user can own private todos")
    user = db.get(User, principal.user.id)
    if user is None or user.disabled:
        raise Forbidden("only a signed-in user can own private todos")
    return user


def _user_by_username(db: Session, username: str) -> User:
    user = db.execute(select(User).where(User.username == username)).scalar_one_or_none()
    if user is None or user.disabled:
        raise ProtocolError(f"unknown owner: {username}")
    return user


def _normalize_reason(reason: str | None) -> str | None:
    if reason is None:
        return None
    cleaned = " ".join(reason.strip().split())
    return cleaned or None


def _next_daily_id(db: Session, prefix_kind: str, column) -> str:
    today = _today().strftime("%Y%m%d")
    prefix = f"{prefix_kind}-{today}-"
    latest = db.execute(
        select(func.max(column)).where(column.like(prefix + "%"))
    ).scalar()
    current_max = int(latest.rsplit("-", 1)[1]) if latest else 0
    serial = _allocate_sequence(db, f"{prefix_kind}-id:{today}", current_max)
    if serial > 999:
        raise ProtocolError(f"daily {prefix_kind} id space exhausted")
    return f"{prefix}{serial:03d}"


def next_proposal_id(db: Session) -> str:
    return _next_daily_id(db, "proposal", TodoProposal.id)


def next_item_id(db: Session) -> str:
    return _next_daily_id(db, "todo", TodoItem.id)


def _next_event_seq(db: Session, *, item_id: str | None, proposal_id: str | None) -> int:
    if item_id:
        current = db.execute(
            select(func.max(TodoEvent.seq)).where(TodoEvent.todo_item_id == item_id)
        ).scalar()
        return _allocate_sequence(db, f"todo-events:item:{item_id}", current or 0)
    if proposal_id:
        current = db.execute(
            select(func.max(TodoEvent.seq)).where(TodoEvent.proposal_id == proposal_id)
        ).scalar()
        return _allocate_sequence(db, f"todo-events:proposal:{proposal_id}", current or 0)
    raise ProtocolError("todo event needs a subject")


def append_todo_event(
    db: Session,
    *,
    principal: Principal,
    event_type: str,
    did: str,
    item: TodoItem | None = None,
    proposal: TodoProposal | None = None,
    reason: str | None = None,
    payload: dict[str, Any] | None = None,
) -> TodoEvent:
    # An event belongs to one subject. Confirm copies the proposal id into
    # the payload so the unique (proposal_id, seq) index is not reused.
    subject_item_id = item.id if item is not None else None
    subject_proposal_id = None if item is not None else (
        proposal.id if proposal is not None else None
    )
    event = TodoEvent(
        todo_item_id=subject_item_id,
        proposal_id=subject_proposal_id,
        seq=_next_event_seq(
            db,
            item_id=subject_item_id,
            proposal_id=subject_proposal_id,
        ),
        event_type=event_type,
        who=_actor_name(principal),
        who_kind=_who_kind(principal),
        did=did[:240],
        reason=(reason[:240] if reason else None),
        payload_json=json.dumps(payload or {}, ensure_ascii=False),
        at=utcnow(),
    )
    db.add(event)
    db.flush()
    return event


def _task_link_of(db: Session, item_id: str) -> TodoTaskLink | None:
    return db.execute(
        select(TodoTaskLink).where(TodoTaskLink.todo_item_id == item_id)
    ).scalar_one_or_none()


def proposal_to_dict(row: TodoProposal) -> dict[str, Any]:
    return {
        "id": row.id,
        "owner_user_id": row.owner_user_id,
        "proposed_by": row.proposed_by,
        "title": row.title,
        "notes": row.notes,
        "due_at": _iso(row.due_at),
        "remind_at": _iso(row.remind_at),
        "source_session_id": row.source_session_id,
        "source_message_id": row.source_message_id,
        "source_channel": row.source_channel,
        "source_backlink": row.source_backlink,
        "dedup_key": row.dedup_key,
        "status": row.status,
        "todo_item_id": row.todo_item_id,
        "created_at": _iso(row.created_at),
        "updated_at": _iso(row.updated_at),
    }


def event_to_dict(row: TodoEvent) -> dict[str, Any]:
    try:
        payload = json.loads(row.payload_json or "{}")
    except json.JSONDecodeError:
        payload = {}
    return {
        "seq": row.seq,
        "event_type": row.event_type,
        "who": row.who,
        "who_kind": row.who_kind,
        "did": row.did,
        "reason": row.reason,
        "payload": payload,
        "at": _iso(row.at),
        "todo_item_id": row.todo_item_id,
        "proposal_id": row.proposal_id,
    }


def reminder_to_dict(row: ReminderDelivery) -> dict[str, Any]:
    return {
        "id": row.id,
        "todo_item_id": row.todo_item_id,
        "owner_user_id": row.owner_user_id,
        "scheduled_for": _iso(row.scheduled_for),
        "channel": row.channel,
        "delivery_key": row.delivery_key,
        "status": row.status,
        "created_at": _iso(row.created_at),
        "delivered_at": _iso(row.delivered_at),
    }


def item_to_dict(
    db: Session,
    row: TodoItem,
    *,
    include_events: bool = False,
) -> dict[str, Any]:
    link = _task_link_of(db, row.id)
    payload = {
        "id": row.id,
        "owner_user_id": row.owner_user_id,
        "title": row.title,
        "notes": row.notes,
        "status": row.status,
        "due_at": _iso(row.due_at),
        "remind_at": _iso(row.remind_at),
        "proposal_id": row.proposal_id,
        "source_session_id": row.source_session_id,
        "source_message_id": row.source_message_id,
        "source_channel": row.source_channel,
        "source_backlink": row.source_backlink,
        "task_id": link.task_id if link is not None else None,
        "created_at": _iso(row.created_at),
        "updated_at": _iso(row.updated_at),
    }
    if include_events:
        events = db.execute(
            select(TodoEvent)
            .where(TodoEvent.todo_item_id == row.id)
            .order_by(TodoEvent.seq)
        ).scalars()
        payload["events"] = [event_to_dict(event) for event in events]
    return payload


def _assert_item_owner_or_compliance(
    db: Session,
    principal: Principal,
    item: TodoItem,
    *,
    reason: str | None,
    action: str,
) -> None:
    assert_not_viewer(principal)
    if principal.kind == "user" and principal.user is not None:
        if principal.user.id == item.owner_user_id:
            return
        if principal.role == "admin":
            cleaned = _normalize_reason(reason)
            if cleaned is None or len(cleaned) < MIN_ACCESS_REASON:
                raise Forbidden(
                    "admin access to private todos requires an explicit reason"
                )
            append_todo_event(
                db,
                principal=principal,
                event_type="admin_access",
                did=f"compliance {action}",
                item=item,
                reason=cleaned,
                payload={"action": action},
            )
            return
    raise Forbidden("private todo is not readable")


def assert_item_readable(
    db: Session,
    principal: Principal,
    item: TodoItem,
    *,
    reason: str | None = None,
) -> None:
    _assert_item_owner_or_compliance(
        db, principal, item, reason=reason, action="read"
    )


def assert_item_writable(principal: Principal, item: TodoItem) -> None:
    assert_not_viewer(principal)
    if principal.kind != "user" or principal.user is None:
        raise Forbidden("only the owner can write a private todo")
    if principal.user.id != item.owner_user_id:
        raise Forbidden("only the owner can write a private todo")


def assert_proposal_readable(
    principal: Principal, proposal: TodoProposal
) -> None:
    assert_not_viewer(principal)
    if principal.kind == "agent" and principal.name == proposal.proposed_by:
        return
    if principal.kind == "user" and principal.user is not None:
        if principal.user.id == proposal.owner_user_id:
            return
    raise Forbidden("private todo proposal is not readable")


def grant_propose_capability(
    db: Session, principal: Principal, actor_id: str
) -> list[str]:
    owner = require_owner_user(db, principal)
    actor = db.get(Actor, actor_id)
    if actor is None or actor.disabled:
        raise ProtocolError(f"unknown actor: {actor_id}")
    if actor.kind != "agent":
        raise ProtocolError("todo:propose can only be granted to an agent")
    grants = grants_of(owner)
    if actor_id not in grants:
        grants.append(actor_id)
        _set_grants(owner, grants)
    return grants_of(owner)


def revoke_propose_capability(
    db: Session, principal: Principal, actor_id: str
) -> list[str]:
    owner = require_owner_user(db, principal)
    grants = [item for item in grants_of(owner) if item != actor_id]
    _set_grants(owner, grants)
    return grants


def list_propose_grants(db: Session, principal: Principal) -> list[str]:
    owner = require_owner_user(db, principal)
    return grants_of(owner)


def _agent_may_propose_for(owner: User, principal: Principal) -> bool:
    return principal.kind == "agent" and principal.name in grants_of(owner)


def submit_proposal(
    db: Session,
    principal: Principal,
    *,
    title: str,
    notes: str = "",
    owner_username: str | None = None,
    due_at: str | dt.date | None = None,
    remind_at: str | dt.datetime | None = None,
    source_session_id: int | None = None,
    source_message_id: str | None = None,
    source_channel: str | None = None,
    source_backlink: str | None = None,
    dedup_key: str | None = None,
) -> TodoProposal:
    assert_not_viewer(principal)
    cleaned_title = (title or "").strip()
    if not cleaned_title:
        raise ProtocolError("title must be non-empty")
    cleaned_dedup = (dedup_key or "").strip() or None

    if principal.kind == "agent":
        if not owner_username:
            raise ProtocolError("owner_username is required for an agent proposal")
        if not cleaned_dedup:
            raise ProtocolError("dedup_key is required for an agent proposal")
        owner = _user_by_username(db, owner_username)
        if not _agent_may_propose_for(owner, principal):
            raise Forbidden("agent is not granted todo:propose by this owner")
    else:
        owner = require_owner_user(db, principal)
        if owner_username and owner_username != owner.username:
            raise Forbidden("a user can only propose into their own inbox")

    if cleaned_dedup:
        existing = db.execute(
            select(TodoProposal).where(
                TodoProposal.owner_user_id == owner.id,
                TodoProposal.dedup_key == cleaned_dedup,
            )
        ).scalar_one_or_none()
        if existing is not None:
            return existing

    row = TodoProposal(
        id=next_proposal_id(db),
        owner_user_id=owner.id,
        proposed_by=_actor_name(principal),
        title=cleaned_title,
        notes=(notes or "").strip(),
        due_at=parse_due_date(due_at),
        remind_at=parse_remind_at(remind_at),
        source_session_id=source_session_id,
        source_message_id=(source_message_id or None),
        source_channel=(source_channel or None),
        source_backlink=(source_backlink or None),
        dedup_key=cleaned_dedup,
        status=PROPOSAL_PENDING,
    )
    db.add(row)
    db.flush()
    append_todo_event(
        db,
        principal=principal,
        event_type="proposed",
        did="submitted todo proposal",
        proposal=row,
        payload={
            "source_session_id": row.source_session_id,
            "source_message_id": row.source_message_id,
            "source_channel": row.source_channel,
            "source_backlink": row.source_backlink,
            "dedup_key": row.dedup_key,
        },
    )
    return row


def get_proposal(db: Session, proposal_id: str) -> TodoProposal | None:
    return db.get(TodoProposal, proposal_id)


def list_proposals(db: Session, principal: Principal) -> list[TodoProposal]:
    assert_not_viewer(principal)
    if principal.kind == "agent":
        return list(
            db.execute(
                select(TodoProposal)
                .where(TodoProposal.proposed_by == principal.name)
                .order_by(TodoProposal.created_at.desc())
            ).scalars()
        )
    owner = require_owner_user(db, principal)
    return list(
        db.execute(
            select(TodoProposal)
            .where(TodoProposal.owner_user_id == owner.id)
            .order_by(TodoProposal.created_at.desc())
        ).scalars()
    )


def _copy_source_fields(proposal: TodoProposal, item: TodoItem) -> None:
    item.source_session_id = proposal.source_session_id
    item.source_message_id = proposal.source_message_id
    item.source_channel = proposal.source_channel
    item.source_backlink = proposal.source_backlink


def confirm_proposal(
    db: Session, principal: Principal, proposal: TodoProposal
) -> TodoItem:
    owner = require_owner_user(db, principal)
    if proposal.owner_user_id != owner.id:
        raise Forbidden("only the owner can confirm a todo proposal")
    if proposal.status == PROPOSAL_CONFIRMED and proposal.todo_item_id:
        item = db.get(TodoItem, proposal.todo_item_id)
        if item is None:
            raise ProtocolError("confirmed proposal is missing its todo")
        return item
    if proposal.status != PROPOSAL_PENDING:
        raise ProtocolError("only a pending proposal can be confirmed")

    item = TodoItem(
        id=next_item_id(db),
        owner_user_id=owner.id,
        title=proposal.title,
        notes=proposal.notes,
        status=ITEM_OPEN,
        due_at=proposal.due_at,
        remind_at=proposal.remind_at,
        proposal_id=proposal.id,
    )
    _copy_source_fields(proposal, item)
    db.add(item)
    db.flush()
    proposal.status = PROPOSAL_CONFIRMED
    proposal.todo_item_id = item.id
    proposal.updated_at = utcnow()
    append_todo_event(
        db,
        principal=principal,
        event_type="confirmed",
        did="confirmed proposal into a private todo",
        item=item,
        proposal=proposal,
        payload={"proposal_id": proposal.id},
    )
    if item.remind_at is not None:
        register_reminder(
            db,
            principal,
            item,
            scheduled_for=item.remind_at,
            channel=REMINDER_CHANNEL_DEFAULT,
        )
    return item


def reject_proposal(
    db: Session, principal: Principal, proposal: TodoProposal, *, note: str = ""
) -> TodoProposal:
    owner = require_owner_user(db, principal)
    if proposal.owner_user_id != owner.id:
        raise Forbidden("only the owner can reject a todo proposal")
    if proposal.status != PROPOSAL_PENDING:
        raise ProtocolError("only a pending proposal can be rejected")
    proposal.status = PROPOSAL_REJECTED
    proposal.updated_at = utcnow()
    append_todo_event(
        db,
        principal=principal,
        event_type="rejected",
        did=(note.strip() or "rejected todo proposal"),
        proposal=proposal,
    )
    return proposal


def create_item(
    db: Session,
    principal: Principal,
    *,
    title: str,
    notes: str = "",
    due_at: str | dt.date | None = None,
    remind_at: str | dt.datetime | None = None,
    source_session_id: int | None = None,
    source_message_id: str | None = None,
    source_channel: str | None = None,
    source_backlink: str | None = None,
) -> TodoItem:
    owner = require_owner_user(db, principal)
    cleaned_title = (title or "").strip()
    if not cleaned_title:
        raise ProtocolError("title must be non-empty")
    item = TodoItem(
        id=next_item_id(db),
        owner_user_id=owner.id,
        title=cleaned_title,
        notes=(notes or "").strip(),
        status=ITEM_OPEN,
        due_at=parse_due_date(due_at),
        remind_at=parse_remind_at(remind_at),
        source_session_id=source_session_id,
        source_message_id=(source_message_id or None),
        source_channel=(source_channel or None),
        source_backlink=(source_backlink or None),
    )
    db.add(item)
    db.flush()
    append_todo_event(
        db,
        principal=principal,
        event_type="created",
        did="created private todo",
        item=item,
    )
    if item.remind_at is not None:
        register_reminder(
            db,
            principal,
            item,
            scheduled_for=item.remind_at,
            channel=REMINDER_CHANNEL_DEFAULT,
        )
    return item


def get_item(db: Session, item_id: str) -> TodoItem | None:
    return db.get(TodoItem, item_id)


def list_items(db: Session, principal: Principal) -> list[TodoItem]:
    owner = require_owner_user(db, principal)
    return list(
        db.execute(
            select(TodoItem)
            .where(TodoItem.owner_user_id == owner.id)
            .order_by(TodoItem.created_at.desc())
        ).scalars()
    )


def list_item_events(db: Session, item: TodoItem) -> list[TodoEvent]:
    return list(
        db.execute(
            select(TodoEvent)
            .where(TodoEvent.todo_item_id == item.id)
            .order_by(TodoEvent.seq)
        ).scalars()
    )


def update_item(
    db: Session,
    principal: Principal,
    item: TodoItem,
    *,
    title: str | None = None,
    notes: str | None = None,
    due_at: str | dt.date | None = None,
    clear_due_at: bool = False,
) -> TodoItem:
    assert_item_writable(principal, item)
    if item.status in {ITEM_DONE, ITEM_CANCELLED}:
        raise ProtocolError("a closed todo cannot be edited")
    changes: dict[str, Any] = {}
    if title is not None:
        cleaned = title.strip()
        if not cleaned:
            raise ProtocolError("title must be non-empty")
        if cleaned != item.title:
            changes["title"] = {"before": item.title, "after": cleaned}
            item.title = cleaned
    if notes is not None and notes != item.notes:
        changes["notes"] = {"before": item.notes, "after": notes}
        item.notes = notes
    if clear_due_at:
        if item.due_at is not None:
            changes["due_at"] = {"before": _iso(item.due_at), "after": None}
            item.due_at = None
    elif due_at is not None:
        parsed = parse_due_date(due_at)
        if parsed != item.due_at:
            changes["due_at"] = {"before": _iso(item.due_at), "after": _iso(parsed)}
            item.due_at = parsed
    if not changes:
        return item
    item.updated_at = utcnow()
    append_todo_event(
        db,
        principal=principal,
        event_type="updated",
        did="updated private todo",
        item=item,
        payload={"changes": changes},
    )
    return item


def _require_active(item: TodoItem, verb: str) -> None:
    if item.status not in ACTIVE_ITEM_STATUSES:
        raise ProtocolError(f"only an open todo can be {verb}")


def complete_item(db: Session, principal: Principal, item: TodoItem) -> TodoItem:
    assert_item_writable(principal, item)
    _require_active(item, "completed")
    item.status = ITEM_DONE
    item.updated_at = utcnow()
    append_todo_event(
        db,
        principal=principal,
        event_type="completed",
        did="completed private todo",
        item=item,
    )
    return item


def cancel_item(db: Session, principal: Principal, item: TodoItem) -> TodoItem:
    assert_item_writable(principal, item)
    _require_active(item, "cancelled")
    item.status = ITEM_CANCELLED
    item.updated_at = utcnow()
    append_todo_event(
        db,
        principal=principal,
        event_type="cancelled",
        did="cancelled private todo",
        item=item,
    )
    return item


def snooze_item(
    db: Session,
    principal: Principal,
    item: TodoItem,
    *,
    due_at: str | dt.date,
    remind_at: str | dt.datetime | None = None,
) -> TodoItem:
    assert_item_writable(principal, item)
    _require_active(item, "snoozed")
    parsed_due = parse_due_date(due_at)
    if parsed_due is None:
        raise ProtocolError("snooze requires a new due_at")
    before = _iso(item.due_at)
    item.due_at = parsed_due
    item.status = ITEM_SNOOZED
    payload: dict[str, Any] = {"due_at": {"before": before, "after": _iso(parsed_due)}}
    if remind_at is not None:
        parsed_remind = parse_remind_at(remind_at)
        item.remind_at = parsed_remind
        payload["remind_at"] = _iso(parsed_remind)
        if parsed_remind is not None:
            register_reminder(
                db,
                principal,
                item,
                scheduled_for=parsed_remind,
                channel=REMINDER_CHANNEL_DEFAULT,
            )
    item.updated_at = utcnow()
    append_todo_event(
        db,
        principal=principal,
        event_type="snoozed",
        did="snoozed private todo",
        item=item,
        payload=payload,
    )
    return item


def _delivery_key(item_id: str, scheduled_for: dt.datetime, channel: str) -> str:
    return f"{item_id}:{_iso(scheduled_for)}:{channel}"


def register_reminder(
    db: Session,
    principal: Principal,
    item: TodoItem,
    *,
    scheduled_for: dt.datetime | str,
    channel: str = REMINDER_CHANNEL_DEFAULT,
) -> ReminderDelivery:
    assert_item_writable(principal, item)
    when = parse_remind_at(scheduled_for)
    if when is None:
        raise ProtocolError("reminder requires scheduled_for")
    slot = (channel or REMINDER_CHANNEL_DEFAULT).strip() or REMINDER_CHANNEL_DEFAULT
    key = _delivery_key(item.id, when, slot)
    existing = db.execute(
        select(ReminderDelivery).where(ReminderDelivery.delivery_key == key)
    ).scalar_one_or_none()
    if existing is not None:
        return existing
    row = ReminderDelivery(
        todo_item_id=item.id,
        owner_user_id=item.owner_user_id,
        scheduled_for=when,
        channel=slot,
        delivery_key=key,
        status="pending",
    )
    db.add(row)
    item.remind_at = when
    item.updated_at = utcnow()
    db.flush()
    append_todo_event(
        db,
        principal=principal,
        event_type="reminder_registered",
        did="registered reminder slot",
        item=item,
        payload={"delivery_key": key, "scheduled_for": _iso(when), "channel": slot},
    )
    return row


def list_due_reminders(
    db: Session,
    principal: Principal,
    *,
    as_of: dt.datetime | None = None,
) -> list[ReminderDelivery]:
    owner = require_owner_user(db, principal)
    horizon = as_of or utcnow()
    if horizon.tzinfo is None:
        horizon = horizon.replace(tzinfo=dt.timezone.utc)
    return list(
        db.execute(
            select(ReminderDelivery)
            .where(
                ReminderDelivery.owner_user_id == owner.id,
                ReminderDelivery.status == "pending",
                ReminderDelivery.scheduled_for <= horizon,
            )
            .order_by(ReminderDelivery.scheduled_for)
        ).scalars()
    )


def promote_item(db: Session, principal: Principal, item: TodoItem) -> tuple[TodoItem, Task]:
    assert_item_writable(principal, item)
    existing = _task_link_of(db, item.id)
    if existing is not None:
        task = db.get(Task, existing.task_id)
        if task is None:
            raise ProtocolError("todo task link is missing its card")
        return item, task
    _require_active(item, "promoted")
    owner = require_owner_user(db, principal)
    if not owner.actor_id:
        raise ProtocolError("promoting a todo requires the owner to have an actor")
    task = create_task(
        db,
        title=item.title,
        created_by=owner.actor_id,
        holder=owner.actor_id,
        due_at=item.due_at,
        refs=(item.id,),
        note=f"promoted from private todo {item.id}",
        event_payload={
            "todo_item_id": item.id,
            "source_session_id": item.source_session_id,
            "source_message_id": item.source_message_id,
            "source_backlink": item.source_backlink,
        },
    )
    db.add(
        TodoTaskLink(
            todo_item_id=item.id,
            task_id=task.id,
            created_by=_actor_name(principal),
        )
    )
    item.status = ITEM_PROMOTED
    item.updated_at = utcnow()
    append_todo_event(
        db,
        principal=principal,
        event_type="promoted",
        did="promoted private todo to a shared task",
        item=item,
        payload={"task_id": task.id},
    )
    db.flush()
    return item, task


def home_inbox(db: Session, principal: Principal) -> dict[str, Any]:
    owner = require_owner_user(db, principal)
    today = _today()
    pending = list(
        db.execute(
            select(TodoProposal)
            .where(
                TodoProposal.owner_user_id == owner.id,
                TodoProposal.status == PROPOSAL_PENDING,
            )
            .order_by(TodoProposal.created_at.desc())
        ).scalars()
    )
    due_today = list(
        db.execute(
            select(TodoItem)
            .where(
                TodoItem.owner_user_id == owner.id,
                TodoItem.status.in_(ACTIVE_ITEM_STATUSES),
                TodoItem.due_at == today,
            )
            .order_by(TodoItem.due_at)
        ).scalars()
    )
    overdue = list(
        db.execute(
            select(TodoItem)
            .where(
                TodoItem.owner_user_id == owner.id,
                TodoItem.status.in_(ACTIVE_ITEM_STATUSES),
                TodoItem.due_at.is_not(None),
                TodoItem.due_at < today,
            )
            .order_by(TodoItem.due_at)
        ).scalars()
    )
    waiting_rows = db.execute(
        select(TodoItem, Task)
        .join(TodoTaskLink, TodoTaskLink.todo_item_id == TodoItem.id)
        .join(Task, Task.id == TodoTaskLink.task_id)
        .where(
            TodoItem.owner_user_id == owner.id,
            TodoItem.status == ITEM_PROMOTED,
            Task.status.notin_(TERMINAL_TASK_STATUSES),
            or_(
                Task.holder != (owner.actor_id or ""),
                Task.holder.is_(None),
            ),
        )
        .order_by(TodoItem.updated_at.desc())
    ).all()
    return {
        "pending_proposals": [proposal_to_dict(row) for row in pending],
        "due_today": [item_to_dict(db, row) for row in due_today],
        "overdue": [item_to_dict(db, row) for row in overdue],
        "waiting_on_others": [
            {
                **item_to_dict(db, item),
                "task_holder": task.holder,
                "task_status": task.status,
            }
            for item, task in waiting_rows
        ],
    }
