"""Distillation candidate routes (session → organisation memory, M0)."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from core.protocol.task import ProtocolError

from ..deps import Principal, get_db, require_auth, wrap_protocol_errors
from ..distill import (
    candidate_to_dict,
    get_candidate,
    list_candidates,
    promote_candidate,
    register_candidate,
    reject_candidate,
)
from ..schemas import DistillCandidateBody, DistillRejectBody

router = APIRouter()


def _candidate_or_404(db: Session, candidate_id: int):
    candidate = get_candidate(db, candidate_id)
    if candidate is None:
        raise HTTPException(status_code=404, detail="distill candidate not found")
    return candidate


@router.get("/api/distill/candidates")
def get_distill_candidates(
    status: str | None = Query(default=None),
    principal: Principal = Depends(require_auth),
    db: Session = Depends(get_db, scope="function"),
) -> list[dict[str, Any]]:
    try:
        rows = list_candidates(db, status=status)
    except ProtocolError as exc:
        raise wrap_protocol_errors(exc) from exc
    return [candidate_to_dict(db, row) for row in rows]


@router.post("/api/distill/candidates")
def post_distill_candidate(
    body: DistillCandidateBody,
    principal: Principal = Depends(require_auth),
    db: Session = Depends(get_db, scope="function"),
) -> dict[str, Any]:
    try:
        row = register_candidate(
            db,
            principal,
            summary=body.summary,
            source_session_id=body.source_session_id,
            origin_ref=body.origin_ref,
            cooldown_hours=body.cooldown_hours,
        )
    except ProtocolError as exc:
        raise wrap_protocol_errors(exc) from exc
    return candidate_to_dict(db, row)


@router.get("/api/distill/candidates/{candidate_id}")
def get_distill_candidate(
    candidate_id: int,
    principal: Principal = Depends(require_auth),
    db: Session = Depends(get_db, scope="function"),
) -> dict[str, Any]:
    row = _candidate_or_404(db, candidate_id)
    return candidate_to_dict(db, row)


@router.post("/api/distill/candidates/{candidate_id}/promote")
def post_distill_promote(
    candidate_id: int,
    principal: Principal = Depends(require_auth),
    db: Session = Depends(get_db, scope="function"),
) -> dict[str, Any]:
    row = _candidate_or_404(db, candidate_id)
    try:
        row = promote_candidate(db, principal, row)
    except ProtocolError as exc:
        raise wrap_protocol_errors(exc) from exc
    return candidate_to_dict(db, row)


@router.post("/api/distill/candidates/{candidate_id}/reject")
def post_distill_reject(
    candidate_id: int,
    body: DistillRejectBody,
    principal: Principal = Depends(require_auth),
    db: Session = Depends(get_db, scope="function"),
) -> dict[str, Any]:
    row = _candidate_or_404(db, candidate_id)
    try:
        row = reject_candidate(db, principal, row, note=body.decision_note)
    except ProtocolError as exc:
        raise wrap_protocol_errors(exc) from exc
    return candidate_to_dict(db, row)
