"""Authenticated operator requests and exact-node quota-only claims."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import quota_refresh as refresh
from ..db import NodeToken, QuotaRefreshBatch, utcnow
from ..deps import authenticate, get_db, require_auth, require_node_credential
from ..schemas import QuotaRefreshBody, QuotaRefreshClaimBody
from ..security import hash_token

router = APIRouter()


def operator_identity(request: Request, db: Session = Depends(get_db, scope="function")):
    principal = authenticate(request, db)
    if principal is None:
        header = request.headers.get("authorization", "")
        token = header[7:].strip() if header.lower().startswith("bearer ") else ""
        node = db.scalar(select(NodeToken).where(NodeToken.token_hash == hash_token(token), NodeToken.disabled.is_(False))) if token else None
        if node:
            raise HTTPException(403, "node credentials cannot request quota refresh")
        raise HTTPException(401, "authentication required")
    return principal


async def bounded_body(request, model):
    content = bytearray()
    async for chunk in request.stream():
        if len(content) + len(chunk) > 16 * 1024:
            raise HTTPException(413, "refresh request too large")
        content.extend(chunk)
    try:
        return model.model_validate_json(content)
    except ValidationError:
        raise HTTPException(422, "invalid refresh request") from None


@router.post("/api/quota/refresh")
async def request_refresh(request: Request, principal=Depends(operator_identity), db: Session = Depends(get_db, scope="function")):
    refresh.require_enabled()
    if not refresh.can_refresh(principal):
        raise HTTPException(403, "quota refresh requires an operator")
    body = await bounded_body(request, QuotaRefreshBody)
    return refresh.start(db, body, principal, utcnow())


@router.get("/api/quota/refresh/{batch_id}")
def refresh_status(batch_id: str, principal=Depends(require_auth), db: Session = Depends(get_db, scope="function")):
    refresh.require_enabled()
    if len(batch_id) > 64:
        raise HTTPException(404, "refresh batch not found")
    refresh.expire(db, utcnow())
    batch = db.get(QuotaRefreshBatch, batch_id)
    if batch is None:
        raise HTTPException(404, "refresh batch not found")
    return refresh.projection(db, batch)


@router.post("/api/nodes/quota/refresh/claim")
async def claim_refresh(request: Request, db: Session = Depends(get_db, scope="function")):
    refresh.require_enabled()
    header = request.headers.get("authorization", "")
    token = header[7:].strip() if header.lower().startswith("bearer ") else ""
    record = db.scalar(select(NodeToken).where(NodeToken.token_hash == hash_token(token))) if token else None
    require_node_credential(request, record.node_id if record else "", db)
    body = await bounded_body(request, QuotaRefreshClaimBody)
    require_node_credential(request, body.node, db)
    result = refresh.claim(db, body.node, utcnow())
    return result if result else Response(status_code=204)
