"""Explicit operator association and read-only selected conversation ranges."""
from __future__ import annotations

import hashlib
import json

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import ValidationError
from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from .. import conversations as cv
from ..db import Actor, RuntimeSession, Task, TaskConversationLink, utcnow
from ..deps import get_db, require_auth
from ..quota_refresh import serialize
from ..schemas import ConversationLinkBody

router = APIRouter()


def require_enabled():
    if not cv.enabled():
        raise HTTPException(404, 'conversations disabled')


@router.get('/api/tasks/{task_id}/conversations')
def task_conversations(task_id: str, response: Response, principal=Depends(require_auth), db: Session=Depends(get_db, scope='function')):
    require_enabled()
    response.headers['Cache-Control'] = 'no-store'
    task = db.get(Task, task_id)
    if not task:
        raise HTTPException(404, 'task not found')
    links = db.scalars(select(TaskConversationLink).where(TaskConversationLink.task_id==task_id)
                       .order_by(TaskConversationLink.linked_at, TaskConversationLink.id))
    items = [value for link in links if (value := cv.projection(db, task, link, principal)) is not None]
    return {'items':items, 'empty_reason':'no_visible_conversations' if not items else None}


@router.post('/api/tasks/{task_id}/conversations/links')
async def link_conversation(task_id: str, request: Request, principal=Depends(require_auth), db: Session=Depends(get_db, scope='function')):
    require_enabled()
    if not cv.admin(principal):
        raise HTTPException(403, 'administrator required')
    content=bytearray()
    async for chunk in request.stream():
        if len(content)+len(chunk)>16384:
            raise HTTPException(413, 'conversation selection too large')
        content.extend(chunk)
    try:
        body=ConversationLinkBody.model_validate_json(content)
    except ValidationError:
        raise HTTPException(422, 'invalid conversation selection') from None
    serialize(db)
    task=db.get(Task, task_id); source=db.get(RuntimeSession, body.session_id)
    if task is None or source is None:
        raise HTTPException(404, 'source or task not found')
    messages=json.loads(source.messages_json)
    last=body.msg_to if body.msg_to is not None else len(messages)
    if source.privacy!='full' or not 0<=body.msg_from<last<=len(messages) or last-body.msg_from>80:
        raise HTTPException(422, 'a retained full-privacy message range is required')
    if body.sender_actor_id and db.get(Actor, body.sender_actor_id) is None:
        raise HTTPException(422, 'unknown sender')
    hashes=[cv.message_hash(message) for message in messages[body.msg_from:last]]
    if cv.locate(messages, hashes) is None:
        raise HTTPException(422, "selected messages are ambiguous")
    key=hashlib.sha256(json.dumps(hashes).encode()).hexdigest()
    old=db.scalar(select(TaskConversationLink).where(TaskConversationLink.task_id==task_id,
        TaskConversationLink.session_id==source.id, TaskConversationLink.selection_key==key,
        TaskConversationLink.revoked_at.is_(None)))
    if old:
        snapshot=json.loads(old.snapshot_json)
        if snapshot['sender']['id']!=body.sender_actor_id or snapshot['capture_mode']!=body.capture_mode:
            raise HTTPException(409, 'range is already associated with different attribution')
        return {'id':old.id,'deduplicated':True,'source_access':'owner_or_admin'}
    if db.scalar(select(func.count()).select_from(TaskConversationLink).where(TaskConversationLink.task_id==task_id))>=200:
        raise HTTPException(409, 'task conversation limit reached')
    identity=(source.actor_id,source.runtime,source.external_id)
    cv.protect_source(db, source, principal)
    snapshot={'source_identity':identity, 'runtime':source.runtime, 'node':source.node,
              'capture_mode':body.capture_mode,'sender':cv.actor_snapshot(db,body.sender_actor_id),
              'receiver':cv.actor_snapshot(db,source.actor_id)}
    link=TaskConversationLink(task_id=task_id,session_id=source.id,selection_key=key,
        linked_by=principal.write_identity,snapshot_json=json.dumps(snapshot),hashes_json=json.dumps(hashes))
    db.add(link);db.flush()
    return {'id':link.id,'deduplicated':False,'source_access':'owner_or_admin',
            'notice':'Legacy source access is now limited to its owner and administrators.'}


@router.post('/api/tasks/{task_id}/conversations/links/{link_id}/revoke')
def revoke_conversation(task_id: str, link_id: int, principal=Depends(require_auth), db: Session=Depends(get_db, scope='function')):
    link=db.get(TaskConversationLink,link_id)
    if not link or link.task_id!=task_id:
        raise HTTPException(404,'conversation not found')
    source=db.get(RuntimeSession,link.session_id)
    if not source or not cv.owner(source,principal):
        raise HTTPException(404,'conversation not found')
    db.execute(update(TaskConversationLink).where(TaskConversationLink.id==link.id,
        TaskConversationLink.revoked_at.is_(None)).values(revoked_at=utcnow(),revoked_by=principal.write_identity))
    return {'status':'revoked'}
