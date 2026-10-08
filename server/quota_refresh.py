"""Durable, fixed-purpose quota refresh requests. No executable input."""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
import uuid

from fastapi import HTTPException
from sqlalchemy import delete, func, select, text, update

from .db import Node, QuotaRefreshBatch, QuotaRefreshRequest, QuotaReport

ACTIVE = ("queued", "claimed")
COOLDOWN_SECONDS = 120
DAILY_LIMIT = 48


def aware(value):
    return value.replace(tzinfo=dt.timezone.utc) if value.tzinfo is None else value.astimezone(dt.timezone.utc)


def enabled():
    return os.environ.get("RETINUE_QUOTA_REFRESH", "1") != "0"


def can_refresh(principal):
    return enabled() and principal.kind == "user" and (
        principal.role == "admin" or (principal.role == "member" and os.environ.get("RETINUE_QUOTA_REFRESH_MEMBERS") == "1")
    )


def require_enabled():
    if not enabled():
        raise HTTPException(404, "quota refresh disabled")


def serialize(db):
    # The existing schema row provides a cross-process writer lock, including
    # SQLite where SELECT FOR UPDATE is ignored. Its value never changes.
    db.flush()
    db.execute(text("UPDATE schema_version SET version = version WHERE id = 1"))
    db.expire_all()


def expire(db, now):
    db.execute(update(QuotaRefreshRequest).where(
        QuotaRefreshRequest.status.in_(ACTIVE), QuotaRefreshRequest.expires_at <= now,
    ).values(status="timeout", completed_at=now))


def prune(db, cutoff):
    db.execute(delete(QuotaRefreshBatch).where(QuotaRefreshBatch.created_at < cutoff))
    db.execute(delete(QuotaRefreshRequest).where(QuotaRefreshRequest.created_at < cutoff))


def projection(db, batch):
    entries = []
    for source in json.loads(batch.entries_json):
        entry = dict(source)
        if source.get("id"):
            row = db.get(QuotaRefreshRequest, source["id"])
            if row is None:
                entry.update(status="timeout", results=[], fetched_at=None)
            else:
                entry.update(status=row.status, results=json.loads(row.result_json),
                             created_at=aware(row.created_at).isoformat(),
                             deadline=aware(row.expires_at).isoformat(),
                             fetched_at=None, received_at=None)
                if row.report_id:
                    report = db.get(QuotaReport, row.report_id)
                    if report:
                        entry["fetched_at"] = aware(report.collected_at).isoformat()
                        entry["received_at"] = aware(report.received_at).isoformat()
        entries.append(entry)
    return {"batch_id": batch.id, "created_at": aware(batch.created_at).isoformat(),
            "requests": entries, "poll_after_ms": 3000}


def start(db, body, principal, now):
    require_enabled()
    if not can_refresh(principal):
        raise HTTPException(403, "quota refresh requires an operator")
    serialize(db)
    expire(db, now)
    intent = json.dumps({"nodes": sorted(body.nodes) if body.nodes else None,
                         "providers": sorted(body.providers) if body.providers else None}, sort_keys=True)
    batch_id = hashlib.sha256(f"{principal.name}:{body.request_key}".encode()).hexdigest() if body.request_key else uuid.uuid4().hex
    old = db.get(QuotaRefreshBatch, batch_id)
    if old:
        if old.requested_by != principal.name or old.intent_json != intent:
            raise HTTPException(409, "refresh key already used for another scope")
        return projection(db, old)
    recent = select(QuotaReport.node_id).where(QuotaReport.received_at >= now - dt.timedelta(days=90))
    nodes = list(db.scalars(select(Node.id).where(Node.membership_status == "admitted", Node.id.in_(recent)).order_by(Node.id)))
    if body.nodes:
        if not set(body.nodes) <= set(nodes):
            raise HTTPException(422, "invalid quota nodes")
        nodes = sorted(body.nodes)
    if not nodes:
        raise HTTPException(409, "no quota nodes")
    entries = []
    today = now.replace(hour=0, minute=0, second=0, microsecond=0)
    for node in nodes:
        active = db.scalar(select(QuotaRefreshRequest).where(QuotaRefreshRequest.node_id == node,
            QuotaRefreshRequest.status.in_(ACTIVE)).order_by(QuotaRefreshRequest.created_at).limit(1))
        if active:
            active_scope = json.loads(active.providers_json) if active.providers_json else None
            if (sorted(active_scope) if active_scope else None) != (sorted(body.providers) if body.providers else None):
                raise HTTPException(409, "a different quota scope is already being queried")
            entries.append({"id": active.id, "node": node, "deduplicated": True})
            continue
        last = db.scalar(select(QuotaRefreshRequest).where(QuotaRefreshRequest.node_id == node)
                         .order_by(QuotaRefreshRequest.created_at.desc()).limit(1))
        count = db.scalar(select(func.count()).select_from(QuotaRefreshRequest).where(
            QuotaRefreshRequest.node_id == node, QuotaRefreshRequest.created_at >= today))
        retry = max(0, COOLDOWN_SECONDS - int((now - aware(last.completed_at)).total_seconds())) if last and last.completed_at else 0
        if count >= DAILY_LIMIT:
            retry = max(retry, int((today + dt.timedelta(days=1) - now).total_seconds()))
        if retry:
            entries.append({"node": node, "status": "cooldown", "retry_after": retry})
            continue
        row = QuotaRefreshRequest(id=uuid.uuid4().hex, node_id=node,
            providers_json=json.dumps(body.providers) if body.providers else None,
            requested_by=principal.name, status="queued", created_at=now,
            expires_at=now + dt.timedelta(seconds=120), result_json="[]")
        db.add(row)
        entries.append({"id": row.id, "node": node, "deduplicated": False})
    if not any(entry.get("id") for entry in entries):
        retry = min(entry["retry_after"] for entry in entries)
        raise HTTPException(429, "quota refresh cooling down", headers={"Retry-After": str(retry)})
    batch = QuotaRefreshBatch(id=batch_id, requested_by=principal.name, created_at=now,
                             entries_json=json.dumps(entries), intent_json=intent)
    db.add(batch)
    db.flush()
    return projection(db, batch)


def claim(db, node, now):
    serialize(db)
    expire(db, now)
    row = db.scalar(select(QuotaRefreshRequest).where(QuotaRefreshRequest.node_id == node,
        QuotaRefreshRequest.status == "queued").order_by(QuotaRefreshRequest.created_at).limit(1))
    if row is None:
        return None
    result = db.execute(update(QuotaRefreshRequest).where(QuotaRefreshRequest.id == row.id,
        QuotaRefreshRequest.status == "queued").values(status="claimed", claimed_at=now,
        expires_at=now + dt.timedelta(seconds=180)))
    if result.rowcount != 1:
        return None
    db.refresh(row)
    return {"id": row.id, "providers": json.loads(row.providers_json) if row.providers_json else None,
            "deadline": aware(row.expires_at).isoformat(), "deadline_in": 180, "type": "quota_refresh"}


def complete(db, body, report, now):
    if not body.refresh_request_id:
        return None
    expire(db, now)
    row = db.get(QuotaRefreshRequest, body.refresh_request_id)
    if not row or row.node_id != body.node or row.status != "claimed" or aware(row.expires_at) <= now:
        return "ignored"
    # Old snapshots cannot serve as evidence for this claim. Scope is the
    # server-issued provider subset, never an account or executable argument.
    from .schemas import quota_timestamp
    lower = aware(row.claimed_at) - dt.timedelta(seconds=120)
    upper = now + dt.timedelta(seconds=120)
    if not lower <= quota_timestamp(body.collected_at) <= upper or aware(report.received_at) < aware(row.claimed_at):
        return "ignored"
    wanted = set(json.loads(row.providers_json)) if row.providers_json else None
    results = [{"provider": p.provider, "status": p.status} for p in body.providers if wanted is None or p.provider in wanted]
    if any(not lower <= quota_timestamp(p.fetched_at) <= upper for p in body.providers if wanted is None or p.provider in wanted):
        return "ignored"
    if wanted:
        seen = {p["provider"] for p in results}
        results.extend({"provider": p, "status": "consent_missing"} for p in sorted(wanted - seen))
    successes = sum(p["status"] == "ok" for p in results)
    outcome = "done" if results and successes == len(results) else "partial" if successes else "failed"
    changed = db.execute(update(QuotaRefreshRequest).where(
        QuotaRefreshRequest.id == row.id, QuotaRefreshRequest.node_id == body.node,
        QuotaRefreshRequest.status == "claimed", QuotaRefreshRequest.expires_at > now,
    ).values(status=outcome, result_json=json.dumps(results), completed_at=now, report_id=report.id)
      .execution_options(synchronize_session=False))
    if changed.rowcount != 1:
        return "ignored"
    db.expire(row)
    db.flush()
    return outcome
