"""Authenticated collaboration reads and identity-bound execution reports."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.exc import IntegrityError, OperationalError
from sqlalchemy.orm import Session

from core.protocol.task import ProtocolError

from .. import collaboration as service
from ..collaboration_context import task_context
from ..collaboration_schemas import (
    CollaborationRetryBody, DelegationBody, DelegationPolicyBody, ExecutionAuthorizationBody,
    RunCreateBody, RunEventBody,
)
from ..deps import Principal, get_db, require_auth, wrap_protocol_errors
from ..helpers import get_task_or_404

router = APIRouter()


def _call(operation, *args, **kwargs):
    try:
        return operation(*args, **kwargs)
    except ProtocolError as exc:
        raise wrap_protocol_errors(exc) from exc
    except OperationalError as exc:
        if "locked" not in str(exc).lower() and "busy" not in str(exc).lower():
            raise
        raise HTTPException(409, "concurrent collaboration write; retry the same idempotency key") from exc
    except IntegrityError as exc:
        # The request dependency rolls back the entire child+parent transaction.
        raise HTTPException(409, "concurrent collaboration write; retry the same idempotency key") from exc


@router.get("/api/tasks/{task_id}/collaboration")
def get_collaboration(task_id: str, principal: Principal = Depends(require_auth),
                      db: Session = Depends(get_db, scope="function")):
    return _call(service.collaboration_projection, db, get_task_or_404(db, task_id), principal)


@router.get("/api/tasks/{task_id}/context")
def get_task_context(task_id: str, principal: Principal = Depends(require_auth),
                     db: Session = Depends(get_db, scope="function")):
    return _call(task_context, db, get_task_or_404(db, task_id), principal)


@router.get("/api/tasks/{task_id}/collaboration/events")
def get_collaboration_events(task_id: str, after_event_id: int = Query(0, ge=0),
                             after_attempt_id: int = Query(0, ge=0),
                             limit: int = Query(100, ge=1, le=500),
                             principal: Principal = Depends(require_auth),
                             db: Session = Depends(get_db, scope="function")):
    if principal.kind == "channel":
        raise HTTPException(403, "channel credentials cannot read shared collaboration context")
    return _call(service.collaboration_events, db, get_task_or_404(db, task_id),
                 after_event_id=after_event_id, after_attempt_id=after_attempt_id, limit=limit)


@router.post("/api/tasks/{task_id}/delegations")
def post_delegation(task_id: str, body: DelegationBody,
                    principal: Principal = Depends(require_auth),
                    db: Session = Depends(get_db, scope="function")):
    return _call(service.delegate, db, get_task_or_404(db, task_id), principal, body)


@router.post("/api/tasks/{task_id}/runs")
def post_run(task_id: str, body: RunCreateBody, principal: Principal = Depends(require_auth),
             db: Session = Depends(get_db, scope="function")):
    return _call(service.start_run, db, get_task_or_404(db, task_id), principal, body)


@router.post("/api/tasks/{task_id}/runs/{run_id}/events")
def post_run_event(task_id: str, run_id: str, body: RunEventBody,
                   principal: Principal = Depends(require_auth),
                   db: Session = Depends(get_db, scope="function")):
    return _call(service.report_run, db, get_task_or_404(db, task_id), principal, run_id, body)


@router.post("/api/tasks/{task_id}/runs/{run_id}/authorize-execution")
def post_execution_authorization(task_id: str, run_id: str, body: ExecutionAuthorizationBody,
                                  principal: Principal = Depends(require_auth),
                                  db: Session = Depends(get_db, scope="function")):
    get_task_or_404(db, task_id)
    return _call(service.authorize_run_execution, db, task_id, run_id, principal,
                 body.lease_term, body.request_key)


@router.post("/api/tasks/{task_id}/collaboration/retry")
def post_collaboration_retry(task_id: str, body: CollaborationRetryBody,
                             principal: Principal = Depends(require_auth),
                             db: Session = Depends(get_db, scope="function")):
    return _call(service.prepare_retry, db, get_task_or_404(db, task_id), principal, body.note)


@router.post("/api/tasks/{task_id}/collaboration/policy")
def post_delegation_policy(task_id: str, body: DelegationPolicyBody,
                           principal: Principal = Depends(require_auth),
                           db: Session = Depends(get_db, scope="function")):
    return _call(service.set_delegation_policy, db, get_task_or_404(db, task_id), principal, body)
