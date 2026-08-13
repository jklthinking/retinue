"""Executor self-registration handshake (intake protocol M0, layer 3).

A new executor's first contact with the board is ``POST /api/enroll``: it
submits its node fingerprint and capability profile and gets back an
application id. The record sits pending until an administrator decides;
approval creates the actor and issues the executor token exactly once.
Before approval the applicant holds no credential, so every board write is
already refused by the normal auth gates.
"""

from __future__ import annotations

import json
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db import Actor, ApiToken, EnrollApplication, utcnow
from ..deps import Principal, get_db, require_admin
from ..schemas import EnrollBody, EnrollDecisionBody
from ..security import hash_token, new_token

router = APIRouter()


def _application_to_dict(app: EnrollApplication) -> dict[str, Any]:
    return {
        "id": app.id,
        "fingerprint": app.fingerprint,
        "requested_actor_id": app.requested_actor_id,
        "display_name": app.display_name,
        "runtime": app.runtime,
        "model": app.model,
        "node_id": app.node_id,
        "capabilities": json.loads(app.capabilities_json or "[]"),
        "status": app.status,
        "decided_by": app.decided_by,
        "decision_note": app.decision_note,
        "created_at": app.created_at.isoformat() if app.created_at else None,
        "decided_at": app.decided_at.isoformat() if app.decided_at else None,
    }


@router.post("/api/enroll")
def post_enroll(
    body: EnrollBody, db: Session = Depends(get_db, scope="function")
) -> dict[str, Any]:
    """Public handshake: record the application; grant nothing by itself."""
    if db.get(Actor, body.requested_actor_id) is not None:
        raise HTTPException(
            status_code=409,
            detail=f"actor id already taken: {body.requested_actor_id}",
        )
    duplicate = db.execute(
        select(EnrollApplication).where(
            EnrollApplication.fingerprint == body.fingerprint,
            EnrollApplication.status.in_(("pending", "approved")),
        )
    ).scalar()
    if duplicate is not None:
        raise HTTPException(
            status_code=409,
            detail=(
                f"节点指纹已登记(application {duplicate.id}, "
                f"{duplicate.status});请勿重复注册"
            ),
        )
    app = EnrollApplication(
        fingerprint=body.fingerprint,
        requested_actor_id=body.requested_actor_id,
        display_name=body.display_name,
        runtime=body.runtime,
        model=body.model,
        node_id=body.node_id,
        capabilities_json=json.dumps(body.capabilities, ensure_ascii=False),
        status="pending",
    )
    db.add(app)
    db.flush()
    return {
        "application_id": app.id,
        "status": "pending",
        "note": "申请已记录,等待管理员批准;批准前不可写板",
    }


@router.get("/api/admin/enroll-applications")
def list_enroll_applications(
    status: str | None = None,
    principal: Principal = Depends(require_admin),
    db: Session = Depends(get_db, scope="function"),
) -> list[dict[str, Any]]:
    query = select(EnrollApplication).order_by(EnrollApplication.created_at)
    if status:
        query = query.where(EnrollApplication.status == status)
    return [_application_to_dict(app) for app in db.execute(query).scalars()]


@router.post("/api/admin/enroll-applications/{application_id}/decide")
def decide_enroll_application(
    application_id: int,
    body: EnrollDecisionBody,
    principal: Principal = Depends(require_admin),
    db: Session = Depends(get_db, scope="function"),
) -> dict[str, Any]:
    """Settle one application. Approval creates the actor and shows the
    executor token exactly once, matching the admin token-issuance flow."""
    app = db.get(EnrollApplication, application_id)
    if app is None:
        raise HTTPException(
            status_code=404, detail=f"unknown application: {application_id}"
        )
    if app.status != "pending":
        raise HTTPException(
            status_code=409,
            detail=f"application already decided: {application_id} ({app.status})",
        )
    app.decided_by = principal.write_identity
    app.decision_note = body.note
    app.decided_at = utcnow()
    if body.decision == "reject":
        app.status = "rejected"
        return {"application": _application_to_dict(app), "token": None}
    actor_id = body.actor_id or app.requested_actor_id
    if db.get(Actor, actor_id) is not None:
        raise HTTPException(status_code=409, detail=f"actor exists: {actor_id}")
    db.add(
        Actor(
            id=actor_id,
            kind="agent",
            display_name=app.display_name,
            runtime=app.runtime,
            model=app.model,
            node=app.node_id,
        )
    )
    token = new_token("rtn")
    db.add(
        ApiToken(
            token_hash=hash_token(token),
            actor_id=actor_id,
            label=f"enroll:{application_id}",
        )
    )
    app.status = "approved"
    return {
        "application": _application_to_dict(app),
        "token": token,
        "note": "仅此一次展示,请立即保存",
    }
