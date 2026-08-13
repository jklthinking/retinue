"""Admin routes: users, onboarding, actor tokens, and node tokens."""

from __future__ import annotations

import datetime as dt
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db import Actor, ApiToken, Node, NodeToken, User, utcnow
from ..deps import ONLINE_WINDOW, Principal, get_db, require_admin
from ..helpers import actor_to_dict, build_orientation_context
from ..membership import admit_node
from ..schemas import (
    NodeAdmissionBody,
    NodeTokenBody,
    OnboardingBody,
    TokenBody,
    TokenRotateBody,
    UserBody,
)
from ..security import hash_password, hash_token, new_token

router = APIRouter()


@router.get("/api/admin/users")
def admin_users(
    principal: Principal = Depends(require_admin), db: Session = Depends(get_db, scope="function")
) -> list[dict[str, Any]]:
    return [
        {
            "username": u.username,
            "role": u.role,
            "display_name": u.display_name,
            "actor_id": u.actor_id,
            "disabled": u.disabled,
        }
        for u in db.execute(select(User).order_by(User.username)).scalars()
    ]


@router.post("/api/admin/users")
def admin_create_user(
    body: UserBody,
    principal: Principal = Depends(require_admin),
    db: Session = Depends(get_db, scope="function"),
) -> dict[str, str]:
    if db.execute(select(User).where(User.username == body.username)).scalar():
        raise HTTPException(status_code=409, detail=f"user exists: {body.username}")
    if body.actor_id and db.get(Actor, body.actor_id) is None:
        raise HTTPException(status_code=422, detail=f"unknown actor: {body.actor_id}")
    db.add(
        User(
            username=body.username,
            password_hash=hash_password(body.password),
            role=body.role,
            display_name=body.display_name,
            actor_id=body.actor_id,
        )
    )
    return {"status": "ok"}


@router.post("/api/admin/onboarding/prepare")
def admin_prepare_onboarding(
    body: OnboardingBody,
    principal: Principal = Depends(require_admin),
    db: Session = Depends(get_db, scope="function"),
) -> dict[str, Any]:
    """Register a new agent and return its one-time credential plus context.

    This prepares the web-side identity. Writing a real runtime Profile or
    external Feishu/Telegram identity remains an explicit follow-up action.
    """
    if db.get(Actor, body.actor_id):
        raise HTTPException(status_code=409, detail=f"actor exists: {body.actor_id}")
    if db.execute(select(User).where(User.username == body.username)).scalar():
        raise HTTPException(status_code=409, detail=f"user exists: {body.username}")
    actor = Actor(
        id=body.actor_id,
        kind="agent",
        display_name=body.display_name,
        role=body.role.strip(),
        goal=body.goal.strip(),
        runtime=body.runtime,
        model=body.model,
        node=body.node,
    )
    db.add(actor)
    db.flush()
    db.add(
        User(
            username=body.username,
            password_hash=hash_password(body.password),
            role="member",
            display_name=body.display_name,
            actor_id=body.actor_id,
        )
    )
    token = new_token("rtn")
    db.add(ApiToken(token_hash=hash_token(token), actor_id=body.actor_id, label=body.label))
    db.flush()
    return {
        "status": "ready_for_profile",
        "actor": actor_to_dict(actor, utcnow() - ONLINE_WINDOW),
        "account": {
            "username": body.username,
            "role": "member",
            "actor_id": body.actor_id,
        },
        "token": token,
        "token_note": "仅此一次展示，请立即保存；不会再次出现在组织上下文包中。",
        "orientation": build_orientation_context(db, Principal(
            kind="agent", name=body.actor_id, actor_id=body.actor_id, role="agent"
        )),
        "next_steps": [
            "把一次性令牌交给 BOT 的安全配置，不要贴进任务正文。",
            "BOT 启动时用 GET /api/orientation/context 刷新组织现状。",
            "需要写入真实 Profile 或外部平台身份时，再执行生产入职确认。",
        ],
    }


def _expiry_from_days(days: int | None) -> dt.datetime | None:
    if days is None:
        return None
    return utcnow() + dt.timedelta(days=days)


@router.post("/api/admin/tokens")
def admin_create_token(
    body: TokenBody,
    principal: Principal = Depends(require_admin),
    db: Session = Depends(get_db, scope="function"),
) -> dict[str, str | None]:
    if db.get(Actor, body.actor_id) is None:
        raise HTTPException(status_code=422, detail=f"unknown actor: {body.actor_id}")
    expires_at = _expiry_from_days(body.expires_in_days)
    token = new_token("rtn")
    db.add(
        ApiToken(
            token_hash=hash_token(token),
            actor_id=body.actor_id,
            label=body.label,
            expires_at=expires_at,
        )
    )
    return {
        "token": token,
        "expires_at": expires_at.isoformat() if expires_at else None,
        "note": "仅此一次展示,请立即保存",
    }


@router.get("/api/admin/tokens")
def admin_tokens(
    principal: Principal = Depends(require_admin), db: Session = Depends(get_db, scope="function")
) -> list[dict[str, Any]]:
    return [
        {
            "id": t.id,
            "actor_id": t.actor_id,
            "label": t.label,
            "disabled": t.disabled,
            "created_at": t.created_at.isoformat() if t.created_at else None,
            "last_used_at": t.last_used_at.isoformat() if t.last_used_at else None,
            "expires_at": t.expires_at.isoformat() if t.expires_at else None,
        }
        for t in db.execute(select(ApiToken).order_by(ApiToken.created_at)).scalars()
    ]


@router.post("/api/admin/tokens/{token_id}/revoke")
def admin_revoke_token(
    token_id: int,
    principal: Principal = Depends(require_admin),
    db: Session = Depends(get_db, scope="function"),
) -> dict[str, Any]:
    """Kill one credential now. The row stays as the audit record of its life."""
    token = db.get(ApiToken, token_id)
    if token is None:
        raise HTTPException(status_code=404, detail=f"unknown token: {token_id}")
    if token.disabled:
        raise HTTPException(
            status_code=409, detail=f"token is already revoked: {token_id}"
        )
    token.disabled = True
    return {"status": "revoked", "id": token.id, "actor_id": token.actor_id}


@router.post("/api/admin/tokens/{token_id}/rotate")
def admin_rotate_token(
    token_id: int,
    body: TokenRotateBody | None = None,
    principal: Principal = Depends(require_admin),
    db: Session = Depends(get_db, scope="function"),
) -> dict[str, Any]:
    """Replace one credential's secret in a single decision.

    The old token stops authenticating immediately; the replacement keeps the
    actor, label, and expiry of the grant unless a new lifetime is given.
    Rotation is not a way to resurrect a revoked credential: that path stays
    an explicit new issuance.
    """
    old = db.get(ApiToken, token_id)
    if old is None:
        raise HTTPException(status_code=404, detail=f"unknown token: {token_id}")
    if old.disabled:
        raise HTTPException(
            status_code=409,
            detail=f"token is revoked and cannot be rotated: {token_id}",
        )
    old.disabled = True
    expires_at = old.expires_at
    if body is not None and body.expires_in_days is not None:
        expires_at = _expiry_from_days(body.expires_in_days)
    token = new_token("rtn")
    replacement = ApiToken(
        token_hash=hash_token(token),
        actor_id=old.actor_id,
        label=old.label,
        expires_at=expires_at,
    )
    db.add(replacement)
    db.flush()
    return {
        "token": token,
        "id": replacement.id,
        "revoked_id": old.id,
        "actor_id": old.actor_id,
        "expires_at": expires_at.isoformat() if expires_at else None,
        "note": "仅此一次展示,请立即保存",
    }


@router.post("/api/admin/node-tokens")
def admin_create_node_token(
    body: NodeTokenBody,
    principal: Principal = Depends(require_admin),
    db: Session = Depends(get_db, scope="function"),
) -> dict[str, str]:
    node = db.get(Node, body.node_id)
    if node is not None and node.membership_status == "retired":
        raise HTTPException(
            status_code=409,
            detail=(
                f"node is retired: {body.node_id}; admit it before issuing a token"
            ),
        )
    admit_node(
        db,
        node_id=body.node_id,
        # ``body.label`` names the credential, not the machine. The first
        # heartbeat or an explicit admission may supply the roster label.
        label="",
        admitted_by=principal.write_identity,
    )
    token = new_token("rnn")
    db.add(
        NodeToken(
            token_hash=hash_token(token),
            node_id=body.node_id,
            label=body.label,
        )
    )
    return {
        "token": token,
        "note": "节点上报专用；节点已准入；仅此一次展示",
    }


@router.post("/api/admin/nodes")
def admin_admit_node(
    body: NodeAdmissionBody,
    principal: Principal = Depends(require_admin),
    db: Session = Depends(get_db, scope="function"),
) -> dict[str, str | None]:
    node, changed = admit_node(
        db,
        node_id=body.node_id,
        label=body.label,
        admitted_by=principal.write_identity,
    )
    if not changed:
        raise HTTPException(
            status_code=409, detail=f"node is already admitted: {body.node_id}"
        )
    return {
        "status": "admitted",
        "node_id": node.id,
        "admitted_by": node.admitted_by,
        "admitted_at": node.admitted_at.isoformat() if node.admitted_at else None,
    }


@router.delete("/api/admin/nodes/{node_id}")
def admin_retire_node(
    node_id: str,
    principal: Principal = Depends(require_admin),
    db: Session = Depends(get_db, scope="function"),
) -> dict[str, str | None]:
    node = db.get(Node, node_id)
    if node is None:
        raise HTTPException(status_code=404, detail=f"unknown node: {node_id}")
    if node.membership_status == "retired":
        raise HTTPException(status_code=409, detail=f"node is already retired: {node_id}")
    now = utcnow()
    node.membership_status = "retired"
    node.retired_by = principal.write_identity
    node.retired_at = now
    for token in db.execute(
        select(NodeToken).where(NodeToken.node_id == node_id)
    ).scalars():
        token.disabled = True
    return {
        "status": "retired",
        "node_id": node.id,
        "retired_by": node.retired_by,
        "retired_at": now.isoformat(),
    }


@router.get("/api/admin/node-tokens")
def admin_node_tokens(
    principal: Principal = Depends(require_admin),
    db: Session = Depends(get_db, scope="function"),
) -> list[dict[str, Any]]:
    return [
        {
            "id": token.id,
            "node_id": token.node_id,
            "label": token.label,
            "disabled": token.disabled,
            "created_at": token.created_at.isoformat() if token.created_at else None,
            "last_used_at": token.last_used_at.isoformat() if token.last_used_at else None,
        }
        for token in db.execute(
            select(NodeToken).order_by(NodeToken.created_at)
        ).scalars()
    ]
