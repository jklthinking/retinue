"""Live runtime discovery and binding read model.

Node reports are observations, never authority by themselves.  The Hub only
materializes a ``LiveSession`` when explicit pane metadata, current occupant,
actor, optional task holder, runtime, and endpoint generation all agree.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import secrets
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..db import (
    Actor,
    ControlEnvelope,
    ControlEvent,
    LiveSession,
    Node,
    SessionEndpointBinding,
    SessionEndpointObservation,
    Task,
    utcnow,
)
from ..deps import Principal, get_db, require_auth, require_node_credential
from ..schemas import (
    ControlAckBody,
    ControlCreateBody,
    ControlPullBody,
    LiveSessionPaneBody,
    LiveSessionProbeBody,
)

router = APIRouter()


def _observation_to_dict(item: SessionEndpointObservation) -> dict[str, Any]:
    return {
        "id": item.id,
        "node_id": item.node_id,
        "backend": item.backend,
        "server_id": item.server_id,
        "endpoint_id": item.endpoint_id,
        "generation": item.backend_generation,
        "runtime": item.runtime,
        "input_mode": item.input_mode,
        "actor_id": item.actor_hint,
        "live_session_id": item.live_session_hint,
        "task_id": item.task_hint,
        "explicit_binding": item.explicit_binding,
        "occupant_verified": item.occupant_verified,
        "control_eligible": item.control_eligible,
        "binding_status": item.binding_status,
        "binding_source": item.binding_source,
        "binding_confidence": item.binding_confidence,
        "state": item.state,
        "state_source": item.state_source,
        "state_confidence": item.state_confidence,
        "command": item.command,
        "cwd_hint": item.cwd_hint,
        "display_location": item.display_location,
        "tmux": json.loads(item.backend_metadata_json),
        "bound_live_session_id": item.bound_live_session_id,
        "observed_at": item.observed_at.isoformat(),
        "updated_at": item.updated_at.isoformat(),
        "disappeared_at": (
            item.disappeared_at.isoformat() if item.disappeared_at else None
        ),
    }


def _invalidate_binding(
    db: Session, binding: SessionEndpointBinding, now
) -> None:
    if binding.invalidated_at is not None:
        return
    binding.invalidated_at = now
    live = db.get(LiveSession, binding.live_session_id)
    if live is not None:
        live.state = "disconnected"
        live.state_source = "node-probe"
        live.state_confidence = 100
        live.ended_at = now
        live.updated_at = now


def _invalidate_endpoint(
    db: Session,
    observation: SessionEndpointObservation,
    now,
) -> None:
    bindings = db.execute(
        select(SessionEndpointBinding).where(
            SessionEndpointBinding.node_id == observation.node_id,
            SessionEndpointBinding.backend == observation.backend,
            SessionEndpointBinding.endpoint_id == observation.endpoint_id,
            SessionEndpointBinding.invalidated_at.is_(None),
        )
    ).scalars()
    for binding in bindings:
        _invalidate_binding(db, binding, now)
    observation.bound_live_session_id = None
    observation.control_eligible = False


def _binding_is_valid(
    db: Session,
    pane: LiveSessionPaneBody,
) -> tuple[Actor | None, Task | None]:
    complete = bool(
        pane.explicit_binding
        and pane.binding_source == "explicit"
        and pane.binding_confidence == 100
        and pane.occupant_verified
        and pane.actor_id
        and pane.live_session_id
        and pane.runtime
        and pane.state != "disconnected"
    )
    if not complete:
        return None, None
    actor = db.get(Actor, pane.actor_id)
    if actor is None or actor.disabled:
        return None, None
    if actor.runtime and actor.runtime != pane.runtime:
        return None, None
    task = db.get(Task, pane.task_id) if pane.task_id else None
    if pane.task_id and (task is None or task.holder != actor.id):
        return None, None
    return actor, task


def _materialize_binding(
    db: Session,
    observation: SessionEndpointObservation,
    pane: LiveSessionPaneBody,
    actor: Actor,
    task: Task | None,
    now,
) -> bool:
    assert pane.live_session_id is not None
    live = db.get(LiveSession, pane.live_session_id)
    if live is None:
        live = LiveSession(
            id=pane.live_session_id,
            actor_id=actor.id,
            runtime=pane.runtime,
            task_id=task.id if task else None,
            execution_mode="interactive",
            started_at=now,
        )
        db.add(live)
        db.flush()
    elif live.actor_id != actor.id or live.runtime != pane.runtime:
        return False

    # A live identity has one active location and an endpoint generation has
    # one active live identity.  Invalidate both sides before rebinding.
    conflicts = db.execute(
        select(SessionEndpointBinding).where(
            SessionEndpointBinding.invalidated_at.is_(None),
            (
                (SessionEndpointBinding.live_session_id == live.id)
                | (
                    (SessionEndpointBinding.node_id == observation.node_id)
                    & (SessionEndpointBinding.backend == observation.backend)
                    & (SessionEndpointBinding.endpoint_id == observation.endpoint_id)
                )
            ),
        )
    ).scalars()
    exact: SessionEndpointBinding | None = None
    for binding in conflicts:
        if (
            binding.live_session_id == live.id
            and binding.node_id == observation.node_id
            and binding.backend == observation.backend
            and binding.endpoint_id == observation.endpoint_id
            and binding.backend_generation == observation.backend_generation
        ):
            exact = binding
        else:
            _invalidate_binding(db, binding, now)

    other_observations = db.execute(
        select(SessionEndpointObservation).where(
            SessionEndpointObservation.id != observation.id,
            SessionEndpointObservation.control_eligible.is_(True),
            (
                (SessionEndpointObservation.bound_live_session_id == live.id)
                | (
                    (SessionEndpointObservation.node_id == observation.node_id)
                    & (SessionEndpointObservation.backend == observation.backend)
                    & (SessionEndpointObservation.endpoint_id == observation.endpoint_id)
                )
            ),
        )
    ).scalars()
    for other in other_observations:
        other.control_eligible = False
        other.bound_live_session_id = None
        other.binding_status = "stale"
        other.updated_at = now

    if exact is None:
        exact = SessionEndpointBinding(
            live_session_id=live.id,
            node_id=observation.node_id,
            backend=observation.backend,
            endpoint_id=observation.endpoint_id,
            backend_generation=observation.backend_generation,
            bound_at=now,
        )
        db.add(exact)
    exact.display_location = observation.display_location
    exact.binding_source = "explicit"
    exact.binding_confidence = 100
    exact.last_verified_at = now

    live.task_id = task.id if task else None
    live.state = pane.state
    live.state_source = pane.state_source
    live.state_confidence = pane.state_confidence
    live.last_seen_at = now
    live.ended_at = None
    live.updated_at = now
    observation.bound_live_session_id = live.id
    observation.binding_status = "bound"
    observation.control_eligible = True
    return True


@router.get("/api/live-sessions")
def list_live_sessions(
    principal: Principal = Depends(require_auth),
    db: Session = Depends(get_db, scope="function"),
) -> list[dict[str, Any]]:
    query = select(SessionEndpointObservation)
    if principal.kind == "agent":
        query = query.where(
            SessionEndpointObservation.bound_live_session_id.in_(
                select(LiveSession.id).where(LiveSession.actor_id == principal.actor_id)
            )
        )
    rows = db.execute(
        query.order_by(
            SessionEndpointObservation.node_id,
            SessionEndpointObservation.display_location,
            SessionEndpointObservation.endpoint_id,
        )
    ).scalars()
    return [_observation_to_dict(item) for item in rows]


@router.post("/api/live-sessions/probe")
def post_live_session_probe(
    body: LiveSessionProbeBody,
    request: Request,
    db: Session = Depends(get_db, scope="function"),
) -> dict[str, Any]:
    require_node_credential(request, body.node_id, db)
    if body.status != "ok" and body.panes:
        raise HTTPException(status_code=422, detail="non-ok probe cannot report panes")
    endpoint_ids = [pane.endpoint_id for pane in body.panes]
    if len(endpoint_ids) != len(set(endpoint_ids)):
        raise HTTPException(status_code=422, detail="endpoint entries must be unique")

    node = db.get(Node, body.node_id)
    assert node is not None
    now = utcnow()
    current = {
        item.endpoint_id: item
        for item in db.execute(
            select(SessionEndpointObservation).where(
                SessionEndpointObservation.node_id == body.node_id,
                SessionEndpointObservation.backend == body.backend,
                SessionEndpointObservation.server_id == body.server_id,
            )
        ).scalars()
    }
    reported: set[str] = set()
    bound = 0
    invalid = 0
    for pane in body.panes:
        reported.add(pane.endpoint_id)
        observation = current.get(pane.endpoint_id)
        if observation is None:
            observation = SessionEndpointObservation(
                node_id=body.node_id,
                backend=body.backend,
                server_id=body.server_id,
                endpoint_id=pane.endpoint_id,
                backend_generation=pane.generation,
                observed_at=now,
            )
            db.add(observation)
            db.flush()
        elif observation.backend_generation != pane.generation:
            _invalidate_endpoint(db, observation, now)

        observation.backend_generation = pane.generation
        observation.runtime = pane.runtime
        observation.input_mode = pane.input_mode
        observation.actor_hint = pane.actor_id
        observation.live_session_hint = pane.live_session_id
        observation.task_hint = pane.task_id
        observation.explicit_binding = pane.explicit_binding
        observation.occupant_verified = pane.occupant_verified
        observation.binding_source = pane.binding_source
        observation.binding_confidence = pane.binding_confidence
        observation.state = pane.state
        observation.state_source = pane.state_source
        observation.state_confidence = pane.state_confidence
        observation.command = pane.command
        observation.cwd_hint = pane.cwd_hint
        observation.display_location = pane.display_location
        observation.backend_metadata_json = json.dumps(pane.tmux.model_dump())
        observation.observed_at = now
        observation.updated_at = now
        observation.disappeared_at = None
        observation.binding_status = "unbound"
        observation.control_eligible = False

        actor, task = _binding_is_valid(db, pane)
        if actor is None:
            _invalidate_endpoint(db, observation, now)
            if pane.explicit_binding:
                observation.binding_status = "invalid"
                invalid += 1
            continue
        if not _materialize_binding(db, observation, pane, actor, task, now):
            _invalidate_endpoint(db, observation, now)
            observation.binding_status = "invalid"
            invalid += 1
            continue
        bound += 1

    disappeared = 0
    for endpoint_id, observation in current.items():
        if endpoint_id in reported or observation.disappeared_at is not None:
            continue
        _invalidate_endpoint(db, observation, now)
        observation.binding_status = "stale"
        observation.disappeared_at = now
        observation.updated_at = now
        disappeared += 1

    node.sessions_probed_at = now
    node.updated_at = now
    db.flush()
    return {
        "status": "ok",
        "node_id": body.node_id,
        "observed": len(body.panes),
        "bound": bound,
        "invalid": invalid,
        "disappeared": disappeared,
        "sessions_probed_at": now.isoformat(),
    }


def _append_control_event(
    db: Session,
    envelope: ControlEnvelope,
    event_type: str,
    *,
    who_kind: str,
    who: str,
    detail: dict[str, Any] | None = None,
) -> None:
    seq = (
        db.execute(
            select(func.max(ControlEvent.seq)).where(
                ControlEvent.envelope_id == envelope.id
            )
        ).scalar_one()
        or 0
    ) + 1
    db.add(
        ControlEvent(
            envelope_id=envelope.id,
            seq=seq,
            event_type=event_type,
            who_kind=who_kind,
            who=who,
            detail_json=json.dumps(detail or {}, sort_keys=True),
        )
    )


def _control_to_dict(envelope: ControlEnvelope) -> dict[str, Any]:
    return {
        "id": envelope.id,
        "live_session_id": envelope.live_session_id,
        "node_id": envelope.node_id,
        "verb": envelope.verb,
        "status": envelope.status,
        "attempts": envelope.attempts,
        "result": json.loads(envelope.result_json),
        "expires_at": envelope.expires_at.isoformat(),
        "leased_at": envelope.leased_at.isoformat() if envelope.leased_at else None,
        "completed_at": (
            envelope.completed_at.isoformat() if envelope.completed_at else None
        ),
        "created_at": envelope.created_at.isoformat(),
        "updated_at": envelope.updated_at.isoformat(),
    }


def _scrub_control_payload(envelope: ControlEnvelope) -> None:
    """Drop transient command content once an envelope reaches a terminal state.

    Request hashes and append-only events retain the audit/idempotency evidence;
    the message itself is not a durable transcript.
    """
    envelope.payload_json = "{}"


def _active_observation(
    db: Session, envelope: ControlEnvelope
) -> SessionEndpointObservation | None:
    return db.execute(
        select(SessionEndpointObservation).where(
            SessionEndpointObservation.node_id == envelope.node_id,
            SessionEndpointObservation.backend == envelope.backend,
            SessionEndpointObservation.endpoint_id == envelope.endpoint_id,
            SessionEndpointObservation.backend_generation
            == envelope.backend_generation,
            SessionEndpointObservation.bound_live_session_id
            == envelope.live_session_id,
            SessionEndpointObservation.control_eligible.is_(True),
            SessionEndpointObservation.disappeared_at.is_(None),
        )
    ).scalar_one_or_none()


@router.post("/api/live-sessions/control/pull")
def pull_live_session_controls(
    body: ControlPullBody,
    request: Request,
    db: Session = Depends(get_db, scope="function"),
) -> dict[str, Any]:
    require_node_credential(request, body.node_id, db)
    now = utcnow()
    expired = list(
        db.execute(
            select(ControlEnvelope).where(
                ControlEnvelope.node_id == body.node_id,
                ControlEnvelope.status == "queued",
                ControlEnvelope.expires_at <= now,
            )
        ).scalars()
    )
    for envelope in expired:
        envelope.status = "expired"
        _scrub_control_payload(envelope)
        envelope.completed_at = now
        envelope.updated_at = now
        _append_control_event(
            db, envelope, "expired", who_kind="hub", who="hub"
        )

    queued = list(
        db.execute(
            select(ControlEnvelope)
            .where(
                ControlEnvelope.node_id == body.node_id,
                ControlEnvelope.status == "queued",
                ControlEnvelope.expires_at > now,
            )
            .order_by(ControlEnvelope.created_at, ControlEnvelope.id)
            .limit(body.limit)
        ).scalars()
    )
    deliveries: list[dict[str, Any]] = []
    for envelope in queued:
        observation = _active_observation(db, envelope)
        if observation is None or (
            envelope.verb == "tell" and observation.input_mode != "codex-prompt"
        ):
            envelope.status = "failed"
            _scrub_control_payload(envelope)
            envelope.completed_at = now
            envelope.updated_at = now
            _append_control_event(
                db,
                envelope,
                "failed",
                who_kind="hub",
                who="hub",
                detail={"reason": "target no longer eligible"},
            )
            continue
        envelope.status = "leased"
        envelope.leased_at = now
        envelope.attempts += 1
        envelope.updated_at = now
        _append_control_event(
            db, envelope, "leased", who_kind="node", who=body.node_id
        )
        deliveries.append(
            {
                "id": envelope.id,
                "live_session_id": envelope.live_session_id,
                "node_id": envelope.node_id,
                "backend": envelope.backend,
                "endpoint_id": envelope.endpoint_id,
                "generation": envelope.backend_generation,
                "verb": envelope.verb,
                "payload": json.loads(envelope.payload_json),
                "actor_id": observation.actor_hint,
                "task_id": observation.task_hint,
                "input_mode": observation.input_mode,
                "tmux": json.loads(observation.backend_metadata_json),
                "expires_at": envelope.expires_at.isoformat(),
            }
        )
    db.flush()
    return {"node_id": body.node_id, "controls": deliveries}


@router.post("/api/live-sessions/control/ack")
def ack_live_session_control(
    body: ControlAckBody,
    request: Request,
    db: Session = Depends(get_db, scope="function"),
) -> dict[str, Any]:
    require_node_credential(request, body.node_id, db)
    envelope = db.get(ControlEnvelope, body.envelope_id)
    if envelope is None:
        raise HTTPException(status_code=404, detail="control envelope not found")
    if envelope.node_id != body.node_id:
        raise HTTPException(status_code=403, detail="control belongs to another node")
    if envelope.backend_generation != body.generation:
        raise HTTPException(status_code=409, detail="endpoint generation mismatch")
    terminal_status = "delivered" if body.outcome == "delivered" else "failed"
    if envelope.status in {"delivered", "failed"}:
        if envelope.status != terminal_status:
            raise HTTPException(status_code=409, detail="conflicting terminal outcome")
        _scrub_control_payload(envelope)
        return _control_to_dict(envelope)
    if envelope.status != "leased":
        raise HTTPException(status_code=409, detail="control envelope is not leased")
    if envelope.verb in {"tell", "interrupt"} and body.result:
        raise HTTPException(
            status_code=422, detail="tell/interrupt acknowledgement has no output"
        )

    now = utcnow()
    envelope.status = terminal_status
    _scrub_control_payload(envelope)
    envelope.result_json = json.dumps(
        {"output": body.result, "detail": body.detail}, sort_keys=True
    )
    envelope.completed_at = now
    envelope.updated_at = now
    _append_control_event(
        db,
        envelope,
        terminal_status,
        who_kind="node",
        who=body.node_id,
        detail={
            "detail": body.detail,
            "result_length": len(body.result),
            "result_hash": hashlib.sha256(body.result.encode()).hexdigest(),
        },
    )
    db.flush()
    return _control_to_dict(envelope)


@router.get("/api/live-sessions/control/{envelope_id}")
def get_live_session_control(
    envelope_id: str,
    principal: Principal = Depends(require_auth),
    db: Session = Depends(get_db, scope="function"),
) -> dict[str, Any]:
    envelope = db.get(ControlEnvelope, envelope_id)
    if envelope is None:
        raise HTTPException(status_code=404, detail="control envelope not found")
    live = db.get(LiveSession, envelope.live_session_id)
    owns_target = live is not None and live.actor_id == principal.actor_id
    owns_request = (
        envelope.requester_kind == principal.kind
        and envelope.requester_id == principal.name
    )
    if not principal.privileged and not owns_target and not owns_request:
        raise HTTPException(status_code=403, detail="control envelope is private")
    return _control_to_dict(envelope)


@router.post("/api/live-sessions/{live_session_id}/control")
def create_live_session_control(
    live_session_id: str,
    body: ControlCreateBody,
    principal: Principal = Depends(require_auth),
    db: Session = Depends(get_db, scope="function"),
) -> dict[str, Any]:
    live = db.get(LiveSession, live_session_id)
    if live is None:
        raise HTTPException(status_code=404, detail="live session not found")
    observation = db.execute(
        select(SessionEndpointObservation).where(
            SessionEndpointObservation.bound_live_session_id == live_session_id,
            SessionEndpointObservation.control_eligible.is_(True),
            SessionEndpointObservation.disappeared_at.is_(None),
        )
    ).scalar_one_or_none()
    if observation is None:
        raise HTTPException(status_code=409, detail="live session has no active endpoint")
    if body.verb in {"peek", "interrupt"} and not (
        principal.privileged or principal.actor_id == live.actor_id
    ):
        raise HTTPException(
            status_code=403,
            detail=f"{body.verb} is limited to operators or owner",
        )
    if body.verb == "tell" and observation.input_mode != "codex-prompt":
        raise HTTPException(
            status_code=409,
            detail="target has no explicit input-mode contract",
        )

    if body.verb == "tell":
        payload = {"message": body.message}
    elif body.verb == "peek":
        payload = {"lines": body.lines}
    else:
        payload = {}
    request_material = json.dumps(
        {
            "live_session_id": live_session_id,
            "generation": observation.backend_generation,
            "verb": body.verb,
            "payload": payload,
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    request_hash = hashlib.sha256(request_material.encode()).hexdigest()
    existing = db.execute(
        select(ControlEnvelope).where(
            ControlEnvelope.requester_kind == principal.kind,
            ControlEnvelope.requester_id == principal.name,
            ControlEnvelope.idempotency_key == body.idempotency_key,
        )
    ).scalar_one_or_none()
    if existing is not None:
        if existing.request_hash != request_hash:
            raise HTTPException(
                status_code=409, detail="idempotency key reused with different request"
            )
        result = _control_to_dict(existing)
        result["created"] = False
        return result

    envelope_id = "ctl-" + secrets.token_hex(12)
    while db.get(ControlEnvelope, envelope_id) is not None:
        envelope_id = "ctl-" + secrets.token_hex(12)
    now = utcnow()
    envelope = ControlEnvelope(
        id=envelope_id,
        requester_kind=principal.kind,
        requester_id=principal.name,
        requester_actor_id=principal.actor_id,
        live_session_id=live.id,
        node_id=observation.node_id,
        backend=observation.backend,
        endpoint_id=observation.endpoint_id,
        backend_generation=observation.backend_generation,
        verb=body.verb,
        payload_json=json.dumps(payload, sort_keys=True),
        request_hash=request_hash,
        idempotency_key=body.idempotency_key,
        status="queued",
        expires_at=now
        + (dt.timedelta(seconds=30) if body.verb == "interrupt" else dt.timedelta(minutes=2)),
        created_at=now,
        updated_at=now,
    )
    db.add(envelope)
    db.flush()
    _append_control_event(
        db,
        envelope,
        "created",
        who_kind=principal.kind,
        who=principal.name,
        detail={"verb": body.verb, "request_hash": request_hash},
    )
    result = _control_to_dict(envelope)
    result["created"] = True
    return result
