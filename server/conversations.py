"""Source-owned access and hash-selected conversation projections."""
from __future__ import annotations

import hashlib
import json
import os

from fastapi import HTTPException
from sqlalchemy import and_, or_, select

from .db import Actor, ConversationProtectedSource, RuntimeSession, Task, TaskConversationLink
from .quota_refresh import aware


def enabled():
    return os.environ.get('RETINUE_TASK_CONVERSATIONS', '0') == '1'


def admin(principal):
    return principal.kind == 'user' and principal.role == 'admin'


def owner(row, principal):
    return admin(principal) or (principal.kind in ('user', 'agent') and principal.actor_id == row.actor_id and
                               (principal.kind != 'user' or principal.role != 'viewer'))


def protected(db, row):
    return db.get(ConversationProtectedSource, row.id) is not None


def require_source_access(db, row, principal):
    marker = db.get(ConversationProtectedSource, row.id)
    mismatch = marker and (marker.actor_id, marker.runtime, marker.external_id) != (row.actor_id, row.runtime, row.external_id)
    if marker and (not owner(row, principal) or (mismatch and not admin(principal))):
        raise HTTPException(404, 'session not found')


def protect_source(db, row, principal):
    """A source actor or administrator may only reduce its legacy audience."""
    if not (admin(principal) or (principal.kind == 'agent' and principal.actor_id == row.actor_id)):
        raise HTTPException(403, 'source actor or administrator required')
    marker = db.get(ConversationProtectedSource, row.id)
    if marker and (marker.actor_id, marker.runtime, marker.external_id) != (row.actor_id, row.runtime, row.external_id):
        raise HTTPException(409, 'source identity changed')
    if marker is None:
        db.add(ConversationProtectedSource(session_id=row.id, actor_id=row.actor_id,
                                          runtime=row.runtime, external_id=row.external_id))


def visible_sources(principal):
    # Filter before search and limit: hidden title/summary cannot become an
    # existence oracle through LIKE, pagination or related-task filters.
    if admin(principal):
        return True
    own = principal.actor_id if (principal.kind == 'agent' or (principal.kind == 'user' and principal.role != 'viewer')) else ''
    owned_sources = select(ConversationProtectedSource.session_id).where(
        ConversationProtectedSource.actor_id == (own or ''),
        ConversationProtectedSource.runtime == RuntimeSession.runtime,
        ConversationProtectedSource.external_id == RuntimeSession.external_id).correlate(RuntimeSession)
    return or_(~RuntimeSession.id.in_(select(ConversationProtectedSource.session_id)),
               and_(RuntimeSession.actor_id == (own or ''), RuntimeSession.id.in_(owned_sources)))


def task_audience(db, task, source, principal):
    if owner(source, principal):
        return True
    actor = db.get(Actor, task.created_by)
    return (principal.kind == 'user' and principal.role != 'viewer' and
            principal.actor_id is not None and principal.actor_id == task.created_by and actor is not None and actor.kind == 'human')


def message_hash(message):
    # Only stable visible native fields. Content is not copied into a link.
    native = {key: message.get(key) for key in ('role', 'at', 'text')}
    return hashlib.sha256(json.dumps(native, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def locate(messages, wanted):
    actual = [message_hash(message) for message in messages]
    positions = [first for first in range(len(actual)-len(wanted)+1)
                 if actual[first:first+len(wanted)] == wanted]
    return messages[positions[0]:positions[0]+len(wanted)] if wanted and len(positions) == 1 else None


def actor_snapshot(db, actor_id):
    actor = db.get(Actor, actor_id) if actor_id else None
    if not actor:
        return {'id': None, 'name': None, 'model': None, 'model_source': 'unknown'}
    model = actor.model.strip() if actor.model else ''
    if model.lower() in ('', 'unknown', 'multi', 'default', 'claude', 'codex', 'kimi', 'grok', 'cursor'):
        model = ''
    return {'id': actor.id, 'name': actor.display_name or actor.id,
            'model': model or None, 'model_source': 'registry' if model else 'unknown'}


def projection(db, task, link, principal):
    source = db.get(RuntimeSession, link.session_id)
    snapshot = json.loads(link.snapshot_json)
    if source is not None and (source.actor_id, source.runtime, source.external_id) != tuple(snapshot['source_identity']):
        return {'id': link.id, 'state': 'source_changed', 'messages': [], 'summary': None,
                'linked_at': aware(link.linked_at).isoformat()} if admin(principal) else None
    if source is None or not task_audience(db, task, source, principal):
        return None
    if link.revoked_at and not owner(source, principal):
        return None
    result = {'id': link.id, 'linked_at': aware(link.linked_at).isoformat(),
              'state': 'available', 'messages': [], 'summary': None}
    if link.revoked_at:
        result.update(state='revoked', revoked_at=aware(link.revoked_at).isoformat(), revoked_by=link.revoked_by)
        return result
    result.update(session_id=source.id, runtime=snapshot['runtime'], node=snapshot['node'],
                  capture_mode=snapshot['capture_mode'], sender=snapshot['sender'], receiver=snapshot['receiver'],
                  identity_source='operator_annotation', model_source=snapshot['receiver']['model_source'])
    if (source.actor_id, source.runtime, source.external_id) != tuple(snapshot['source_identity']):
        result['state'] = 'source_changed'
        return result
    if source.privacy != 'full':
        result['state'] = 'metadata_only'
        return result
    messages = json.loads(source.messages_json)
    selected = locate(messages, json.loads(link.hashes_json))
    if selected is None:
        result['state'] = 'outside_window'
        return result
    # Task owners can see only the selected range, not a full-session summary.
    if owner(source, principal):
        result['summary'] = source.summary or None
    result['messages'] = [dict(role=item['role'], text=item['text'], at=item.get('at'),
        sender=snapshot['sender'] if item['role']=='user' else snapshot['receiver'],
        receiver=snapshot['receiver'] if item['role']=='user' else snapshot['sender'],
        identity_source='operator_annotation' if item['role']=='user' else 'session_metadata')
        for item in selected if item.get('role') in ('user', 'assistant')]
    result.update(retained_messages=len(messages), source_message_count=source.message_count,
                  linked_messages=len(selected), coverage='selected_range')
    return result
