"""Knowledge source catalog routes."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db import KnowledgeSource
from ..deps import Principal, get_db, require_admin, require_auth
from ..schemas import KnowledgeBody

router = APIRouter()


def knowledge_to_dict(source: KnowledgeSource) -> dict[str, Any]:
    return {
        "id": source.id,
        "name": source.name,
        "kind": source.kind,
        "location": source.location,
        "docs": source.docs,
        "size_bytes": source.size_bytes,
        "notes": source.notes,
        "updated_at": source.updated_at.isoformat() if source.updated_at else None,
    }


@router.get("/api/knowledge")
def get_knowledge(
    principal: Principal = Depends(require_auth), db: Session = Depends(get_db, scope="function")
) -> list[dict[str, Any]]:
    return [
        knowledge_to_dict(k)
        for k in db.execute(
            select(KnowledgeSource).order_by(KnowledgeSource.kind, KnowledgeSource.name)
        ).scalars()
    ]


@router.post("/api/knowledge")
def post_knowledge(
    body: KnowledgeBody,
    principal: Principal = Depends(require_admin),
    db: Session = Depends(get_db, scope="function"),
) -> dict[str, Any]:
    source = db.execute(
        select(KnowledgeSource).where(KnowledgeSource.name == body.name)
    ).scalar()
    if source is None:
        source = KnowledgeSource(**body.model_dump())
        db.add(source)
    else:
        for key, value in body.model_dump().items():
            setattr(source, key, value)
    db.flush()
    return knowledge_to_dict(source)
