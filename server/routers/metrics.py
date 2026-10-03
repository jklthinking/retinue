"""Metrics routes: token usage ingest, summary, and task throughput."""

from __future__ import annotations

import datetime as dt
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..db import Actor, TaskEvent, TokenUsage
from ..deps import Principal, get_db, require_auth
from ..discovery import is_sync_actor
from ..schemas import MetricsBody
from ..usage_accounting import STALE_AFTER_SECONDS, calendar_timezone, input_definition, report_freshness, utc_now

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
    try:
        dt.date.fromisoformat(body.date)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail="date must be a real calendar date") from exc
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
        # This is report receipt time, not the time of the underlying model
        # activity. Even a repeated absolute report is a new receipt.
        row.updated_at = utc_now()
    return {"status": "ok"}


@router.get("/api/metrics/summary")
def metrics_summary(
    days: int = 7,
    timezone: str = "local",
    principal: Principal = Depends(require_auth),
    db: Session = Depends(get_db, scope="function"),
) -> dict[str, Any]:
    days = max(1, min(days, 31))
    now = utc_now()
    try:
        end_date = now.astimezone(calendar_timezone(timezone)).date()
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    start = (end_date - dt.timedelta(days=days - 1)).isoformat()
    end = end_date.isoformat()
    rows = list(db.execute(
        select(TokenUsage).where(TokenUsage.date >= start, TokenUsage.date <= end)
        .order_by(TokenUsage.date, TokenUsage.actor_id, TokenUsage.runtime)
    ).scalars())
    actors = {actor.id: actor for actor in db.execute(select(Actor)).scalars()}
    latest = {
        actor_id: (updated_at, date)
        for actor_id, updated_at, date in db.execute(
            select(TokenUsage.actor_id, func.max(TokenUsage.updated_at), func.max(TokenUsage.date))
            .where(TokenUsage.date <= end).group_by(TokenUsage.actor_id)
        )
    }
    by_actor: dict[str, dict[str, Any]] = {}
    invalid_rows = excluded_transport_rows = 0
    for row in rows:
        if row.actor_id in actors and is_sync_actor(actors[row.actor_id]):
            excluded_transport_rows += 1
            continue
        try:
            dt.date.fromisoformat(row.date)
        except ValueError:
            invalid_rows += 1
            continue
        entry = by_actor.setdefault(
            row.actor_id, {"actor_id": row.actor_id, "days": {}, "input": 0, "output": 0,
                           "usage_available": True, "records": 0, "runtimes": {}}
        )
        day = entry["days"].setdefault(row.date, {"input": 0, "output": 0})
        day["input"] += row.input_tokens
        day["output"] += row.output_tokens
        entry["input"] += row.input_tokens
        entry["output"] += row.output_tokens
        entry["records"] += 1
        runtime = entry["runtimes"].setdefault(row.runtime, {
            "runtime": row.runtime, "input": 0, "output": 0, "records": 0,
            "input_definition": input_definition(row.runtime), "cache_breakdown": None,
            "source": "runtime_daily_report", "_updated_at": None,
        })
        runtime["input"] += row.input_tokens
        runtime["output"] += row.output_tokens
        runtime["records"] += 1
        if runtime["_updated_at"] is None or row.updated_at > runtime["_updated_at"]:
            runtime["_updated_at"] = row.updated_at
    expected = {actor.id for actor in actors.values() if actor.kind == "agent" and not actor.disabled and not is_sync_actor(actor)}
    for actor_id in expected:
        by_actor.setdefault(actor_id, {
            "actor_id": actor_id, "days": {}, "input": None, "output": None,
            "usage_available": False, "records": 0, "runtimes": {},
        })
    for actor_id, entry in by_actor.items():
        actor = actors.get(actor_id)
        entry.update({"node": actor.node if actor else "", "model": actor.model if actor else "",
                      "runtime": actor.runtime if actor else "", "disabled": bool(actor and actor.disabled)})
        reported_at, last_date = latest.get(actor_id, (None, None))
        entry.update(report_freshness(reported_at, now))
        entry["last_usage_date"] = last_date
        entry["provenance"] = "legacy_daily_report; source identity and bucket timezone unrecorded"
        for runtime in entry["runtimes"].values():
            runtime.update(report_freshness(runtime.pop("_updated_at"), now))
        entry["runtimes"] = list(entry["runtimes"].values())
    reported = {actor_id for actor_id in expected if by_actor[actor_id]["usage_available"]}
    missing = sorted(expected - reported)
    complete = bool(expected) and all(len(by_actor[actor_id]["days"]) == days for actor_id in expected)
    available = any(entry["usage_available"] for entry in by_actor.values())
    return {
        "source": "retinue_daily_usage", "generated_at": now.isoformat(),
        "start": start, "end": end, "days": days,
        "timezone": timezone if timezone != "local" else "service-local(" + now.astimezone().strftime("%z") + ")",
        "bucket_timezone": "reporter-local-unrecorded",
        "accounting": "absolute daily replacement per actor/date/runtime; different collectors do not add",
        "scope": "reported runtime usage; no task allocation or inferred usage",
        "usage_available": available,
        "stale_after_seconds": STALE_AFTER_SECONDS,
        "coverage": {"expected_actors": len(expected), "reported_actors": len(reported),
                     "missing_actors": missing, "complete": complete,
                     "basis": "daily report presence; underlying usage completeness unknown"},
        "totals": {"input": sum(entry["input"] or 0 for entry in by_actor.values()) if available else None,
                   "output": sum(entry["output"] or 0 for entry in by_actor.values()) if available else None},
        "diagnostics": {"invalid_window_rows": invalid_rows,
                        "excluded_transport_rows": excluded_transport_rows,
                        "excluded_future_rows": db.scalar(select(func.count()).select_from(TokenUsage).where(TokenUsage.date > end))},
        "actors": [by_actor[key] for key in sorted(by_actor)],
    }


@router.get("/api/metrics/throughput")
def throughput(
    days: int = 14,
    timezone: str = "local",
    principal: Principal = Depends(require_auth),
    db: Session = Depends(get_db, scope="function"),
) -> dict[str, Any]:
    days = max(1, min(days, 60))
    now = utc_now()
    try:
        calendar = calendar_timezone(timezone)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    end_date = now.astimezone(calendar).date()
    start_date = end_date - dt.timedelta(days=days - 1)
    start, end = start_date.isoformat(), end_date.isoformat()
    # ISO timestamps can carry an offset. Widen the SQL date prefilter by a
    # day, then compare parsed instants in the requested calendar below.
    events = db.execute(
        select(TaskEvent).where(
            func.substr(TaskEvent.at, 1, 10) >= (start_date - dt.timedelta(days=1)).isoformat(),
            func.substr(TaskEvent.at, 1, 10) <= (end_date + dt.timedelta(days=1)).isoformat(),
        )
    ).scalars()
    by_day: dict[str, dict[str, int]] = {}
    by_actor: dict[str, int] = {}
    invalid = timezone_unknown = future = 0
    for event in events:
        try:
            timestamp = dt.datetime.fromisoformat(event.at.replace("Z", "+00:00"))
        except (AttributeError, ValueError):
            invalid += 1
            continue
        if timestamp.tzinfo is None:
            # New ledger events are fixed-width UTC. A legacy naive value
            # has no reliable source timezone; do not guess it.
            timezone_unknown += 1
            continue
        if timestamp > now:
            future += 1
            continue
        day = timestamp.astimezone(calendar).date().isoformat()
        if not start <= day <= end:
            continue
        bucket = by_day.setdefault(day, {"done": 0, "receipts": 0})
        bucket["receipts"] += 1
        if event.to_status == "done" and event.from_status != "done":
            bucket["done"] += 1
            by_actor[event.who] = by_actor.get(event.who, 0) + 1
    return {
        "start": start,
        "end": end,
        "timezone": timezone if timezone != "local" else "service-local(" + now.astimezone().strftime("%z") + ")",
        "generated_at": now.isoformat(),
        "source": "task_event_ledger",
        "diagnostics": {"invalid_timestamps": invalid, "timezone_unknown_timestamps": timezone_unknown,
                        "future_events_in_candidate_window": future},
        "days": [{"date": d, **v} for d, v in sorted(by_day.items())],
        "done_by_actor": [
            {"actor_id": a, "done": n}
            for a, n in sorted(by_actor.items(), key=lambda kv: -kv[1])
        ],
    }
