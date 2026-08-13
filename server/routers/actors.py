"""Actor registry routes, plus agent matching and runtime discovery."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db import Actor, Node, NodeRuntime, RuntimeSession, utcnow
from ..deps import ONLINE_WINDOW, Principal, get_db, require_admin, require_auth
from ..discovery import runtime_label, scan_local_runtimes
from ..helpers import actor_to_dict
from ..matching import match_agents
from ..schemas import ActorBody, ActorUpdateBody

router = APIRouter()


@router.get("/api/actors")
def actors(
    principal: Principal = Depends(require_auth), db: Session = Depends(get_db, scope="function")
) -> list[dict[str, Any]]:
    cutoff = utcnow() - ONLINE_WINDOW
    return [
        actor_to_dict(a, cutoff)
        for a in db.execute(select(Actor).order_by(Actor.kind, Actor.id)).scalars()
    ]


@router.post("/api/actors")
def create_actor(
    body: ActorBody,
    principal: Principal = Depends(require_admin),
    db: Session = Depends(get_db, scope="function"),
) -> dict[str, Any]:
    if db.get(Actor, body.id):
        raise HTTPException(status_code=409, detail=f"actor exists: {body.id}")
    actor = Actor(**body.model_dump())
    db.add(actor)
    db.flush()
    return actor_to_dict(actor, utcnow() - ONLINE_WINDOW)


@router.post("/api/actors/{actor_id}/update")
def update_actor_profile(
    actor_id: str,
    body: ActorUpdateBody,
    principal: Principal = Depends(require_admin),
    db: Session = Depends(get_db, scope="function"),
) -> dict[str, Any]:
    actor = db.get(Actor, actor_id)
    if actor is None:
        raise HTTPException(status_code=404, detail=f"unknown actor: {actor_id}")
    for field, value in body.model_dump(exclude_none=True).items():
        setattr(actor, field, value.strip() if isinstance(value, str) else value)
    db.flush()
    return actor_to_dict(actor, utcnow() - ONLINE_WINDOW)


@router.get("/api/agent-match")
def agent_match(
    q: str = "",
    limit: int = 8,
    principal: Principal = Depends(require_auth),
    db: Session = Depends(get_db, scope="function"),
) -> list[dict[str, Any]]:
    """Return an explainable, offline ranking of enabled agents."""
    if len(q) > 500:
        raise HTTPException(status_code=422, detail="搜索内容不能超过 500 字")
    return match_agents(
        db,
        q,
        limit=limit,
        online_cutoff=utcnow() - ONLINE_WINDOW,
    )


@router.get("/api/agent-discovery")
def agent_discovery(
    principal: Principal = Depends(require_auth),
    db: Session = Depends(get_db, scope="function"),
) -> dict[str, Any]:
    """Return a safe, explainable inventory of connected runtimes.

    Local scans and opted-in node probes only report runtime availability.
    Session metadata may enrich the inventory, but this endpoint never reads
    transcript text, terminal configuration, environment variables, or credentials.
    """
    cutoff = utcnow() - ONLINE_WINDOW
    actor_rows = list(db.execute(select(Actor).order_by(Actor.id)).scalars())
    agent_rows = [row for row in actor_rows if row.kind == "agent" and not row.disabled]
    session_rows = list(db.execute(select(RuntimeSession)).scalars())
    node_rows = list(
        db.execute(
            select(Node)
            .where(Node.membership_status == "admitted")
            .order_by(Node.id)
        ).scalars()
    )
    node_by_id = {row.id: row for row in node_rows}
    probe_rows = list(
        db.execute(
            select(NodeRuntime).where(
                NodeRuntime.available.is_(True),
                NodeRuntime.node_id.in_(node_by_id),
            )
        ).scalars()
    )
    local_rows = {row["runtime"]: row for row in scan_local_runtimes()}

    runtimes = set(local_rows)
    runtimes.update(row.runtime.strip() for row in agent_rows if row.runtime.strip())
    runtimes.update(row.runtime.strip() for row in session_rows if row.runtime.strip())
    runtimes.update(row.runtime.strip() for row in probe_rows if row.runtime.strip())

    runtime_items: list[dict[str, Any]] = []
    for runtime in sorted(runtimes):
        related_agents = [row for row in agent_rows if row.runtime.strip() == runtime]
        related_sessions = [row for row in session_rows if row.runtime.strip() == runtime]
        related_probes = [row for row in probe_rows if row.runtime.strip() == runtime]
        local = local_rows.get(runtime)
        latest_session = max(
            (row.synced_at for row in related_sessions if row.synced_at is not None),
            default=None,
        )
        latest_probe = max(
            (row.detected_at for row in related_probes if row.detected_at is not None),
            default=None,
        )
        source = (
            "本机扫描"
            if local
            else (
                "节点探针"
                if related_probes
                else ("会话同步" if related_sessions else "成员登记")
            )
        )
        nodes = [
            {
                "id": row.node_id,
                "label": (node_by_id.get(row.node_id).label if node_by_id.get(row.node_id) else row.node_id),
                "detected_at": row.detected_at.isoformat() if row.detected_at else None,
            }
            for row in sorted(related_probes, key=lambda item: item.node_id)
        ]
        runtime_items.append(
            {
                "runtime": runtime,
                "label": runtime_label(runtime),
                "source": source,
                "local_detected": bool(local),
                "path_hint": local.get("path_hint") if local else None,
                "last_changed_at": local.get("last_changed_at") if local else None,
                "session_count": len(related_sessions),
                "last_activity_at": latest_session.isoformat() if latest_session else None,
                "last_probe_at": latest_probe.isoformat() if latest_probe else None,
                "nodes": nodes,
                "agent_ids": [row.id for row in related_agents],
                "registered": bool(related_agents),
            }
        )

    session_actors = {row.actor_id for row in session_rows}
    probed_nodes = {row.node_id for row in probe_rows}
    available_pairs = {(row.node_id, row.runtime) for row in probe_rows}
    attention: list[dict[str, Any]] = []
    for actor in agent_rows:
        missing: list[str] = []
        runtime = actor.runtime.strip()
        node = actor.node.strip()
        if not runtime:
            missing.append("运行时")
        if not node:
            missing.append("运行节点")
        if actor.id not in session_actors:
            missing.append("会话同步")
        if runtime and node and node in probed_nodes and (node, runtime) not in available_pairs:
            missing.append("节点未发现该运行时")
        if missing:
            attention.append(
                {
                    "actor_id": actor.id,
                    "display_name": actor.display_name or actor.id,
                    "runtime": actor.runtime,
                    "node": actor.node,
                    "missing": missing,
                    "online": bool(
                        actor.last_seen_at
                        and actor.last_seen_at > cutoff.replace(tzinfo=None)
                    ),
                }
            )

    actions: list[str] = []
    unregistered = [row for row in runtime_items if not row["registered"]]
    if unregistered:
        actions.append(f"有 {len(unregistered)} 个运行时尚未关联智能体；确认后即可登记。")
    if not probe_rows:
        actions.append("尚未收到跨机运行时探针；在每台终端每日运行 probe-runtimes 后即可发现可用 CLI。")
    if attention:
        actions.append(f"有 {len(attention)} 位智能体需要补齐绑定、节点能力或会话同步。")
    if not actions:
        actions.append("已发现的运行时均有成员绑定；可直接到协作空间按能力派单。")

    return {
        "scanned_at": utcnow().isoformat(),
        "scope": "本机目录 + 节点 PATH 探针 + 已接入终端的会话元数据",
        "privacy": "不读取会话正文、提示词、密钥或绝对路径",
        "runtimes": runtime_items,
        "attention": attention,
        "actions": actions,
    }
