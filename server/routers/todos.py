"""Private todo routes: inbox, proposals, reminders, and task promotion."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from core.protocol.task import ProtocolError

from ..deps import Principal, get_db, require_auth, wrap_protocol_errors
from ..engine import task_to_dict
from ..helpers import notify_feishu
from ..schemas import (
    TodoCreateBody,
    TodoGrantBody,
    TodoProposalBody,
    TodoRejectBody,
    TodoReminderBody,
    TodoSnoozeBody,
    TodoUpdateBody,
)
from ..todos import (
    assert_item_readable,
    assert_item_writable,
    assert_not_viewer,
    assert_proposal_readable,
    cancel_item,
    complete_item,
    confirm_proposal,
    create_item,
    get_item,
    get_proposal,
    grant_propose_capability,
    home_inbox,
    item_to_dict,
    list_due_reminders,
    list_item_events,
    list_items,
    list_proposals,
    list_propose_grants,
    parse_remind_at,
    promote_item,
    proposal_to_dict,
    register_reminder,
    reject_proposal,
    reminder_to_dict,
    revoke_propose_capability,
    event_to_dict,
    snooze_item,
    submit_proposal,
    update_item,
)

router = APIRouter()


def _item_or_404(db: Session, item_id: str):
    item = get_item(db, item_id)
    if item is None:
        raise HTTPException(status_code=404, detail="todo not found")
    return item


def _proposal_or_404(db: Session, proposal_id: str):
    proposal = get_proposal(db, proposal_id)
    if proposal is None:
        raise HTTPException(status_code=404, detail="todo proposal not found")
    return proposal


@router.get("/api/todos/home")
def get_todo_home(
    principal: Principal = Depends(require_auth),
    db: Session = Depends(get_db, scope="function"),
) -> dict[str, Any]:
    try:
        return home_inbox(db, principal)
    except ProtocolError as exc:
        raise wrap_protocol_errors(exc) from exc


@router.get("/api/todos/reminders/due")
def get_due_reminders(
    as_of: str | None = None,
    principal: Principal = Depends(require_auth),
    db: Session = Depends(get_db, scope="function"),
) -> dict[str, Any]:
    try:
        horizon = parse_remind_at(as_of) if as_of else None
        rows = list_due_reminders(db, principal, as_of=horizon)
    except ProtocolError as exc:
        raise wrap_protocol_errors(exc) from exc
    return {"reminders": [reminder_to_dict(row) for row in rows]}


@router.get("/api/todos/grants")
def get_todo_grants(
    principal: Principal = Depends(require_auth),
    db: Session = Depends(get_db, scope="function"),
) -> dict[str, Any]:
    try:
        return {"capability": "todo:propose", "actor_ids": list_propose_grants(db, principal)}
    except ProtocolError as exc:
        raise wrap_protocol_errors(exc) from exc


@router.post("/api/todos/grants")
def post_todo_grant(
    body: TodoGrantBody,
    principal: Principal = Depends(require_auth),
    db: Session = Depends(get_db, scope="function"),
) -> dict[str, Any]:
    try:
        actor_ids = grant_propose_capability(db, principal, body.actor_id)
    except ProtocolError as exc:
        raise wrap_protocol_errors(exc) from exc
    return {"capability": "todo:propose", "actor_ids": actor_ids}


@router.delete("/api/todos/grants/{actor_id}")
def delete_todo_grant(
    actor_id: str,
    principal: Principal = Depends(require_auth),
    db: Session = Depends(get_db, scope="function"),
) -> dict[str, Any]:
    try:
        actor_ids = revoke_propose_capability(db, principal, actor_id)
    except ProtocolError as exc:
        raise wrap_protocol_errors(exc) from exc
    return {"capability": "todo:propose", "actor_ids": actor_ids}


@router.get("/api/todos/proposals")
def get_todo_proposals(
    principal: Principal = Depends(require_auth),
    db: Session = Depends(get_db, scope="function"),
) -> dict[str, Any]:
    try:
        rows = list_proposals(db, principal)
    except ProtocolError as exc:
        raise wrap_protocol_errors(exc) from exc
    return {"proposals": [proposal_to_dict(row) for row in rows]}


@router.post("/api/todos/proposals")
def post_todo_proposal(
    body: TodoProposalBody,
    principal: Principal = Depends(require_auth),
    db: Session = Depends(get_db, scope="function"),
) -> dict[str, Any]:
    try:
        row = submit_proposal(
            db,
            principal,
            title=body.title,
            notes=body.notes,
            owner_username=body.owner_username,
            due_at=body.due_at,
            remind_at=body.remind_at,
            source_session_id=body.source_session_id,
            source_message_id=body.source_message_id,
            source_channel=body.source_channel,
            source_backlink=body.source_backlink,
            dedup_key=body.dedup_key,
        )
    except ProtocolError as exc:
        raise wrap_protocol_errors(exc) from exc
    return proposal_to_dict(row)


@router.get("/api/todos/proposals/{proposal_id}")
def get_todo_proposal(
    proposal_id: str,
    principal: Principal = Depends(require_auth),
    db: Session = Depends(get_db, scope="function"),
) -> dict[str, Any]:
    proposal = _proposal_or_404(db, proposal_id)
    try:
        assert_proposal_readable(principal, proposal)
    except ProtocolError as exc:
        raise wrap_protocol_errors(exc) from exc
    return proposal_to_dict(proposal)


@router.post("/api/todos/proposals/{proposal_id}/confirm")
def post_todo_proposal_confirm(
    proposal_id: str,
    principal: Principal = Depends(require_auth),
    db: Session = Depends(get_db, scope="function"),
) -> dict[str, Any]:
    proposal = _proposal_or_404(db, proposal_id)
    try:
        item = confirm_proposal(db, principal, proposal)
    except ProtocolError as exc:
        raise wrap_protocol_errors(exc) from exc
    return item_to_dict(db, item, include_events=True)


@router.post("/api/todos/proposals/{proposal_id}/reject")
def post_todo_proposal_reject(
    proposal_id: str,
    body: TodoRejectBody,
    principal: Principal = Depends(require_auth),
    db: Session = Depends(get_db, scope="function"),
) -> dict[str, Any]:
    proposal = _proposal_or_404(db, proposal_id)
    try:
        row = reject_proposal(db, principal, proposal, note=body.note)
    except ProtocolError as exc:
        raise wrap_protocol_errors(exc) from exc
    return proposal_to_dict(row)


@router.get("/api/todos")
def get_todos(
    principal: Principal = Depends(require_auth),
    db: Session = Depends(get_db, scope="function"),
) -> dict[str, Any]:
    try:
        rows = list_items(db, principal)
    except ProtocolError as exc:
        raise wrap_protocol_errors(exc) from exc
    return {"todos": [item_to_dict(db, row) for row in rows]}


@router.post("/api/todos")
def post_todo(
    body: TodoCreateBody,
    principal: Principal = Depends(require_auth),
    db: Session = Depends(get_db, scope="function"),
) -> dict[str, Any]:
    try:
        item = create_item(
            db,
            principal,
            title=body.title,
            notes=body.notes,
            due_at=body.due_at,
            remind_at=body.remind_at,
            source_session_id=body.source_session_id,
            source_message_id=body.source_message_id,
            source_channel=body.source_channel,
            source_backlink=body.source_backlink,
        )
    except ProtocolError as exc:
        raise wrap_protocol_errors(exc) from exc
    return item_to_dict(db, item, include_events=True)


@router.get("/api/todos/{item_id}")
def get_todo(
    item_id: str,
    reason: str | None = Query(default=None),
    principal: Principal = Depends(require_auth),
    db: Session = Depends(get_db, scope="function"),
) -> dict[str, Any]:
    try:
        assert_not_viewer(principal)
    except ProtocolError as exc:
        raise wrap_protocol_errors(exc) from exc
    item = _item_or_404(db, item_id)
    try:
        assert_item_readable(db, principal, item, reason=reason)
    except ProtocolError as exc:
        raise wrap_protocol_errors(exc) from exc
    return item_to_dict(db, item, include_events=True)


@router.get("/api/todos/{item_id}/events")
def get_todo_events(
    item_id: str,
    reason: str | None = Query(default=None),
    principal: Principal = Depends(require_auth),
    db: Session = Depends(get_db, scope="function"),
) -> dict[str, Any]:
    try:
        assert_not_viewer(principal)
    except ProtocolError as exc:
        raise wrap_protocol_errors(exc) from exc
    item = _item_or_404(db, item_id)
    try:
        assert_item_readable(db, principal, item, reason=reason)
        events = list_item_events(db, item)
    except ProtocolError as exc:
        raise wrap_protocol_errors(exc) from exc
    return {"events": [event_to_dict(event) for event in events]}


@router.post("/api/todos/{item_id}/update")
def post_todo_update(
    item_id: str,
    body: TodoUpdateBody,
    principal: Principal = Depends(require_auth),
    db: Session = Depends(get_db, scope="function"),
) -> dict[str, Any]:
    item = _item_or_404(db, item_id)
    try:
        assert_item_writable(principal, item)
        item = update_item(
            db,
            principal,
            item,
            title=body.title,
            notes=body.notes,
            due_at=body.due_at,
            clear_due_at=body.due_at == "",
        )
    except ProtocolError as exc:
        raise wrap_protocol_errors(exc) from exc
    return item_to_dict(db, item, include_events=True)


@router.post("/api/todos/{item_id}/complete")
def post_todo_complete(
    item_id: str,
    principal: Principal = Depends(require_auth),
    db: Session = Depends(get_db, scope="function"),
) -> dict[str, Any]:
    item = _item_or_404(db, item_id)
    try:
        item = complete_item(db, principal, item)
    except ProtocolError as exc:
        raise wrap_protocol_errors(exc) from exc
    return item_to_dict(db, item, include_events=True)


@router.post("/api/todos/{item_id}/cancel")
def post_todo_cancel(
    item_id: str,
    principal: Principal = Depends(require_auth),
    db: Session = Depends(get_db, scope="function"),
) -> dict[str, Any]:
    item = _item_or_404(db, item_id)
    try:
        item = cancel_item(db, principal, item)
    except ProtocolError as exc:
        raise wrap_protocol_errors(exc) from exc
    return item_to_dict(db, item, include_events=True)


@router.post("/api/todos/{item_id}/snooze")
def post_todo_snooze(
    item_id: str,
    body: TodoSnoozeBody,
    principal: Principal = Depends(require_auth),
    db: Session = Depends(get_db, scope="function"),
) -> dict[str, Any]:
    item = _item_or_404(db, item_id)
    try:
        item = snooze_item(
            db, principal, item, due_at=body.due_at, remind_at=body.remind_at
        )
    except ProtocolError as exc:
        raise wrap_protocol_errors(exc) from exc
    return item_to_dict(db, item, include_events=True)


@router.post("/api/todos/{item_id}/reminders")
def post_todo_reminder(
    item_id: str,
    body: TodoReminderBody,
    principal: Principal = Depends(require_auth),
    db: Session = Depends(get_db, scope="function"),
) -> dict[str, Any]:
    item = _item_or_404(db, item_id)
    try:
        row = register_reminder(
            db,
            principal,
            item,
            scheduled_for=body.scheduled_for,
            channel=body.channel,
        )
    except ProtocolError as exc:
        raise wrap_protocol_errors(exc) from exc
    return reminder_to_dict(row)


@router.post("/api/todos/{item_id}/promote")
def post_todo_promote(
    item_id: str,
    principal: Principal = Depends(require_auth),
    db: Session = Depends(get_db, scope="function"),
) -> dict[str, Any]:
    item = _item_or_404(db, item_id)
    try:
        item, task = promote_item(db, principal, item)
    except ProtocolError as exc:
        raise wrap_protocol_errors(exc) from exc
    result = item_to_dict(db, item, include_events=True)
    result["task"] = task_to_dict(task)
    notify_feishu(
        f"【私人事务升级】{item.id} → {task.id} {task.title}"
    )
    return result
