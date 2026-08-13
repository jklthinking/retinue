"""Health, site configuration, status, and the dashboard read model."""

from __future__ import annotations

import hmac
import os
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .. import __version__
from ..db import Actor, Approval, KnowledgeSource, Node, Skill, Task, utcnow
from ..deps import ONLINE_WINDOW, Principal, get_db, require_auth, site_config
from ..engine import list_tasks, task_to_dict
from ..license import load_license

router = APIRouter()


@router.get("/api/health")
def health() -> dict[str, str]:
    return {"status": "ok", "version": __version__}


@router.get("/api/login-config")
def login_config(request: Request) -> dict[str, Any]:
    config = site_config(request.app.state.data_dir)
    label = config.get("label", "")
    return {
        "label": label,
        "demo": bool(config.get("demo_user")),
        "mode": config.get("mode", ""),
        "entry_label": config.get("entry_label", ""),
        "footnote": config.get("footnote", ""),
        "sites": [
            {**site, "current": site.get("label") == label}
            for site in config.get("sites", [])
            if isinstance(site, dict)
        ],
    }


@router.get("/api/status")
def status(
    request: Request,
    principal: Principal = Depends(require_auth), db: Session = Depends(get_db, scope="function")
) -> dict[str, Any]:
    counts = dict(
        db.execute(
            select(Task.status, func.count()).where(Task.archived.is_(False)).group_by(Task.status)
        ).all()
    )
    online_cutoff = utcnow() - ONLINE_WINDOW
    online = db.execute(
        select(func.count())
        .select_from(Actor)
        .where(Actor.last_seen_at.is_not(None))
        .where(Actor.last_seen_at > online_cutoff.replace(tzinfo=None))
    ).scalar()
    return {
        "version": __version__,
        "task_counts": counts,
        "actors": db.execute(select(func.count()).select_from(Actor)).scalar(),
        "online_actors": online,
        "skills": db.execute(select(func.count()).select_from(Skill)).scalar(),
        "nodes": db.execute(select(func.count()).select_from(Node)).scalar(),
        "knowledge_sources": db.execute(
            select(func.count()).select_from(KnowledgeSource)
        ).scalar(),
        "license": load_license(request.app.state.data_dir),
    }


@router.get("/api/dashboard/overview")
def dashboard_overview(
    request: Request,
    db: Session = Depends(get_db, scope="function"),
) -> dict[str, Any]:
    """Authenticated read model for dashboards; it has no mutation route."""
    expected = os.environ.get("RETINUE_DASHBOARD_TOKEN", "")
    supplied = request.headers.get("x-retinue-dashboard-token", "")
    if not expected or not supplied or not hmac.compare_digest(expected, supplied):
        raise HTTPException(status_code=401, detail="dashboard read token required")

    actors = {
        actor.id: actor
        for actor in db.execute(select(Actor).order_by(Actor.id)).scalars()
    }
    pending = {
        approval.task_id: approval.status
        for approval in db.execute(
            select(Approval).where(Approval.status == "pending")
        ).scalars()
    }
    items: list[dict[str, Any]] = []
    for task in list_tasks(db, include_archived=False):
        snapshot = task_to_dict(task)
        stages = snapshot.get("pipeline") or []
        stage_index = int(snapshot.get("pipeline_stage") or 0)
        current_stage = stages[stage_index] if 0 <= stage_index < len(stages) else None
        actor = actors.get(task.holder)
        items.append({
            "id": snapshot["id"],
            "title": snapshot["title"],
            "priority": snapshot["priority"],
            "status": snapshot["status"],
            "holder": snapshot["holder"],
            "blocked_reason": snapshot["blocked_reason"],
            "progress": snapshot["progress"],
            "pipeline": snapshot["pipeline"],
            "pipeline_stage": snapshot["pipeline_stage"],
            "created_at": snapshot["created_at"],
            "updated_at": snapshot["updated_at"],
            "holder_display": (
                actor.display_name if actor and actor.display_name else task.holder
            ),
            "node": actor.node if actor and actor.node else "throne",
            "current_stage": current_stage,
            "approval_status": pending.get(task.id),
            "refs": snapshot["refs"],
            "reviews": snapshot["reviews"],
        })
    return {
        "source": "retinue-api",
        "generated_at": utcnow().isoformat(),
        "tasks": items,
        "actors": [
            {
                "id": actor.id,
                "display_name": actor.display_name,
                "kind": actor.kind,
                "node": actor.node,
            }
            for actor in actors.values()
        ],
    }
