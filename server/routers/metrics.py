"""Metrics routes: token usage ingest, summary, and task throughput."""

from __future__ import annotations

import datetime as dt
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..db import Actor, TaskEvent, TokenUsage
from ..deps import Principal, get_db, require_auth
from ..schemas import MetricsBody

router = APIRouter()


@router.post("/api/metrics/ingest")
def ingest_metrics(
    body: MetricsBody,
    principal: Principal = Depends(require_auth),
    db: Session = Depends(get_db, scope="function"),
) -> dict[str, str]:
    if not principal.privileged and principal.actor_id != body.actor_id:
        raise HTTPException(status_code=403, detail="agents may only report their own usage")
    if db.get(Actor, body.actor_id) is None:
        raise HTTPException(status_code=422, detail=f"unknown actor: {body.actor_id}")
    row = db.execute(
        select(TokenUsage)
        .where(TokenUsage.actor_id == body.actor_id)
        .where(TokenUsage.date == body.date)
        .where(TokenUsage.runtime == body.runtime)
    ).scalar()
    if row is None:
        db.add(TokenUsage(**body.model_dump()))
    else:
        row.input_tokens = body.input_tokens
        row.output_tokens = body.output_tokens
    return {"status": "ok"}


@router.get("/api/metrics/summary")
def metrics_summary(
    days: int = 7,
    principal: Principal = Depends(require_auth),
    db: Session = Depends(get_db, scope="function"),
) -> dict[str, Any]:
    days = max(1, min(days, 31))
    start = (dt.date.today() - dt.timedelta(days=days - 1)).isoformat()
    rows = db.execute(
        select(TokenUsage).where(TokenUsage.date >= start).order_by(TokenUsage.date)
    ).scalars()
    by_actor: dict[str, dict[str, Any]] = {}
    for row in rows:
        entry = by_actor.setdefault(
            row.actor_id, {"actor_id": row.actor_id, "days": {}, "input": 0, "output": 0}
        )
        day = entry["days"].setdefault(row.date, {"input": 0, "output": 0})
        day["input"] += row.input_tokens
        day["output"] += row.output_tokens
        entry["input"] += row.input_tokens
        entry["output"] += row.output_tokens
    return {"start": start, "days": days, "actors": list(by_actor.values())}


@router.get("/api/metrics/throughput")
def throughput(
    days: int = 14,
    principal: Principal = Depends(require_auth),
    db: Session = Depends(get_db, scope="function"),
) -> dict[str, Any]:
    days = max(1, min(days, 60))
    start = (dt.date.today() - dt.timedelta(days=days - 1)).isoformat()
    events = db.execute(
        select(TaskEvent).where(func.substr(TaskEvent.at, 1, 10) >= start)
    ).scalars()
    by_day: dict[str, dict[str, int]] = {}
    by_actor: dict[str, int] = {}
    for event in events:
        day = event.at[:10]
        bucket = by_day.setdefault(day, {"done": 0, "receipts": 0})
        bucket["receipts"] += 1
        if event.to_status == "done" and event.from_status != "done":
            bucket["done"] += 1
            by_actor[event.who] = by_actor.get(event.who, 0) + 1
    return {
        "start": start,
        "days": [{"date": d, **v} for d, v in sorted(by_day.items())],
        "done_by_actor": [
            {"actor_id": a, "done": n}
            for a, n in sorted(by_actor.items(), key=lambda kv: -kv[1])
        ],
    }
