"""Node routes: inventory, heartbeats, and runtime probes."""

from __future__ import annotations

import json
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from core.protocol.task import ProtocolError

from ..db import Actor, Node, NodeRuntime, utcnow
from ..deps import (
    Principal,
    get_db,
    require_auth,
    require_node_credential,
    require_node_heartbeat,
    wrap_protocol_errors,
)
from ..engine import append_attempt, attempt_to_dict
from ..helpers import get_task_or_404
from ..schemas import HeartbeatBody, NodeAttemptBody, RuntimeProbeBody
from ..watermarks import compute_watermark, evaluate_and_maybe_open_card, load_watermarks_config

router = APIRouter()


def node_runtime_to_dict(item: NodeRuntime, data_probed: bool) -> dict[str, Any]:
    # "unknown" (an older probe cannot check data directories) must never
    # collapse into "none" (a current probe checked and found none).
    if item.path_hint:
        data_state = "present"
    elif data_probed:
        data_state = "none"
    else:
        data_state = "unknown"
    return {
        "node_id": item.node_id,
        "runtime": item.runtime,
        "command": item.command,
        "available": item.available,
        "source": item.source,
        "path_hint": item.path_hint,
        "data_changed_at": (
            item.data_changed_at.isoformat() if item.data_changed_at else None
        ),
        "data_state": data_state,
        "detected_at": item.detected_at.isoformat() if item.detected_at else None,
        "updated_at": item.updated_at.isoformat() if item.updated_at else None,
    }


def node_to_dict(
    node: Node,
    runtimes: list[NodeRuntime],
    watermark: dict[str, str] | None = None,
) -> dict[str, Any]:
    # Three states a person must tell apart, as an explicit field rather
    # than a magic value: never probed, probed and found nothing, probed
    # and found agents (a CLI or a data directory both count as found).
    if node.runtimes_probed_at is None:
        runtime_state = "never_probed"
    elif any(item.available or item.path_hint for item in runtimes):
        runtime_state = "probed_found"
    else:
        runtime_state = "probed_empty"
    data_probed = node.data_dirs_probed_at is not None
    return {
        "id": node.id,
        "label": node.label,
        "hostname": node.hostname,
        "platform": node.platform,
        "uptime_seconds": node.uptime_seconds,
        "load": json.loads(node.load_json),
        "disk": json.loads(node.disk_json),
        "memory": json.loads(node.memory_json),
        "services": json.loads(node.services_json),
        "membership_status": node.membership_status,
        "admitted_by": node.admitted_by,
        "admitted_at": node.admitted_at.isoformat() if node.admitted_at else None,
        "runtimes_probed_at": (
            node.runtimes_probed_at.isoformat() if node.runtimes_probed_at else None
        ),
        "data_dirs_probed_at": (
            node.data_dirs_probed_at.isoformat() if node.data_dirs_probed_at else None
        ),
        "runtime_state": runtime_state,
        "runtimes": [node_runtime_to_dict(item, data_probed) for item in runtimes],
        "updated_at": node.updated_at.isoformat() if node.updated_at else None,
        "watermark": watermark
        or {
            "disk": "unknown",
            "load": "unknown",
        },
    }


@router.get("/api/nodes")
def get_nodes(
    request: Request,
    principal: Principal = Depends(require_auth),
    db: Session = Depends(get_db, scope="function"),
) -> list[dict[str, Any]]:
    nodes = list(
        db.execute(
            select(Node)
            .where(Node.membership_status == "admitted")
            .order_by(Node.id)
        ).scalars()
    )
    runtimes_by_node: dict[str, list[NodeRuntime]] = {}
    for row in db.execute(select(NodeRuntime).order_by(NodeRuntime.runtime)).scalars():
        runtimes_by_node.setdefault(row.node_id, []).append(row)
    config = load_watermarks_config(request.app.state.data_dir)
    result: list[dict[str, Any]] = []
    for node in nodes:
        watermark = compute_watermark(
            json.loads(node.disk_json),
            json.loads(node.load_json),
            config,
        )
        result.append(
            node_to_dict(node, runtimes_by_node.get(node.id, []), watermark=watermark)
        )
    return result


@router.post("/api/nodes/heartbeat")
def node_heartbeat(
    body: HeartbeatBody,
    request: Request,
    db: Session = Depends(get_db, scope="function"),
) -> dict[str, Any]:
    require_node_heartbeat(request, body.id, db)
    node = db.get(Node, body.id)
    assert node is not None  # admission is checked with the credential
    node.label = body.label or node.label or body.id
    node.hostname = body.hostname
    node.platform = body.platform
    node.uptime_seconds = body.uptime_seconds
    node.load_json = json.dumps(body.load)
    node.disk_json = json.dumps(body.disk)
    node.memory_json = json.dumps(body.memory)
    node.services_json = json.dumps(body.services)
    node.updated_at = utcnow()
    watermark = evaluate_and_maybe_open_card(
        db,
        node_id=body.id,
        disk=body.disk,
        load=body.load,
        data_dir=request.app.state.data_dir,
    )
    return {"status": "ok", "watermark": watermark}


@router.get("/api/nodes/{node_id}/runtimes")
def get_node_runtimes(
    node_id: str,
    principal: Principal = Depends(require_auth),
    db: Session = Depends(get_db, scope="function"),
) -> list[dict[str, Any]]:
    node = db.get(Node, node_id)
    data_probed = node is not None and node.data_dirs_probed_at is not None
    return [
        node_runtime_to_dict(item, data_probed)
        for item in db.execute(
            select(NodeRuntime)
            .where(NodeRuntime.node_id == node_id)
            .order_by(NodeRuntime.runtime)
        ).scalars()
    ]


@router.post("/api/nodes/runtimes")
def post_node_runtimes(
    body: RuntimeProbeBody,
    request: Request,
    db: Session = Depends(get_db, scope="function"),
) -> dict[str, Any]:
    require_node_heartbeat(request, body.node_id, db)
    runtime_names = [item.runtime for item in body.runtimes]
    if len(runtime_names) != len(set(runtime_names)):
        raise HTTPException(status_code=422, detail="runtime entries must be unique")
    data_dir_names = [item.runtime for item in body.data_dirs or []]
    if len(data_dir_names) != len(set(data_dir_names)):
        raise HTTPException(
            status_code=422, detail="data directory entries must be unique"
        )
    node = db.get(Node, body.node_id)
    assert node is not None  # admission is checked with the credential
    current = {
        item.runtime: item
        for item in db.execute(
            select(NodeRuntime).where(NodeRuntime.node_id == body.node_id)
        ).scalars()
    }
    now = utcnow()
    reported = set(runtime_names)
    for body_item in body.runtimes:
        item = current.get(body_item.runtime)
        if item is None:
            item = NodeRuntime(node_id=body.node_id, runtime=body_item.runtime)
            db.add(item)
            current[body_item.runtime] = item
        item.command = body_item.command
        item.available = body_item.available
        item.source = body_item.source
        item.detected_at = now
        item.updated_at = now
    for runtime, item in current.items():
        if runtime not in reported:
            item.available = False
            item.updated_at = now
    if body.data_dirs is not None:
        # A current probe checked the data directories, so absence is a
        # fact: clear hints this report no longer finds.
        node.data_dirs_probed_at = now
        data_by_runtime = {item.runtime: item for item in body.data_dirs}
        for runtime, data_item in data_by_runtime.items():
            item = current.get(runtime)
            if item is None:
                # Local history with no CLI on PATH: the row exists
                # because of the data signal alone.
                item = NodeRuntime(
                    node_id=body.node_id,
                    runtime=runtime,
                    command="",
                    available=False,
                    source="",
                )
                db.add(item)
                current[runtime] = item
            item.path_hint = data_item.path_hint
            item.data_changed_at = data_item.last_changed_at
            item.updated_at = now
        for runtime, item in current.items():
            if runtime not in data_by_runtime and item.path_hint is not None:
                item.path_hint = None
                item.data_changed_at = None
                item.updated_at = now
    # An empty report still proves the probe ran; record the fact on the
    # node, not on rows an empty report does not create.
    node.runtimes_probed_at = now
    node.updated_at = now
    db.flush()
    runtimes = list(
        db.execute(
            select(NodeRuntime)
            .where(NodeRuntime.node_id == body.node_id)
            .order_by(NodeRuntime.runtime)
        ).scalars()
    )
    return {
        "status": "ok",
        "node_id": body.node_id,
        "runtimes_probed_at": now.isoformat(),
        "runtimes": [
            node_runtime_to_dict(item, node.data_dirs_probed_at is not None)
            for item in runtimes
        ],
    }


@router.post("/api/nodes/{node_id}/tasks/{task_id}/attempts")
def post_node_task_attempt(
    node_id: str,
    task_id: str,
    body: NodeAttemptBody,
    request: Request,
    db: Session = Depends(get_db, scope="function"),
) -> dict[str, Any]:
    require_node_credential(request, node_id, db)
    task = get_task_or_404(db, task_id)
    holder = db.get(Actor, task.holder)
    if holder is None or holder.node != node_id:
        raise HTTPException(
            status_code=403,
            detail="node may only report duties for a task held on that node",
        )
    try:
        attempt, created = append_attempt(
            db,
            task,
            reporter_kind="node",
            reporter_id=node_id,
            duty=body.duty,
            outcome=body.outcome,
            started_at=body.started_at,
            ended_at=body.ended_at,
            reason=body.reason,
            exit_status=body.exit_status,
            idempotency_key=body.idempotency_key,
        )
    except ProtocolError as exc:
        raise wrap_protocol_errors(exc) from exc
    return {
        "task_id": task.id,
        "task_status": task.status,
        "created": created,
        "attempt": attempt_to_dict(attempt),
    }
