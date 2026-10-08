"""Bounded node quota telemetry and authenticated read projections."""
from __future__ import annotations

import datetime as dt
import json

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import ValidationError
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from ..db import NodeToken, QuotaReport, QuotaSnapshot, utcnow
from ..deps import get_db, require_auth, require_node_credential, require_node_heartbeat
from ..schemas import QuotaReportBody, quota_timestamp
from ..security import hash_token
from .. import quota_refresh as refresh

router = APIRouter()
MAX_BODY_BYTES = 256 * 1024


def aware(stamp):
    return stamp.replace(tzinfo=dt.timezone.utc) if stamp.tzinfo is None else stamp.astimezone(dt.timezone.utc)


def windows(row):
    return sorted(json.loads(row.windows), key=lambda item: (
        item['resets_at'] is None,
        quota_timestamp(item['resets_at']) if item['resets_at'] else dt.datetime.max.replace(tzinfo=dt.timezone.utc),
        item['key'],
    ))


@router.post('/api/nodes/quota')
async def report_quota(request: Request, db: Session = Depends(get_db, scope='function')):
    # Authenticate before consuming an untrusted body; check exact node below.
    header = request.headers.get('authorization', '')
    token = header[7:].strip() if header.lower().startswith('bearer ') else ''
    record = db.execute(select(NodeToken).where(NodeToken.token_hash == hash_token(token))).scalar()
    require_node_credential(request, record.node_id if record else '', db)
    chunks = bytearray()
    async for chunk in request.stream():
        if len(chunks) + len(chunk) > MAX_BODY_BYTES:
            raise HTTPException(413, 'quota report too large')
        chunks.extend(chunk)
    try:
        body = QuotaReportBody.model_validate_json(chunks)
    except ValidationError:
        # Never reflect submitted secrets in Pydantic's input diagnostics.
        raise HTTPException(422, 'invalid quota report') from None
    require_node_heartbeat(request, body.node, db)
    if body.refresh_request_id:
        refresh.serialize(db)
    now = utcnow()
    cutoff = now - dt.timedelta(days=90)
    refresh.prune(db, cutoff)
    old = select(QuotaReport.id).where(QuotaReport.received_at < cutoff)
    db.execute(delete(QuotaSnapshot).where(QuotaSnapshot.report_id.in_(old)))
    db.execute(delete(QuotaReport).where(QuotaReport.received_at < cutoff))
    report = QuotaReport(node_id=body.node, collected_at=quota_timestamp(body.collected_at), received_at=now)
    db.add(report)
    db.flush()
    for item in body.providers:
        values = item.model_dump()
        values['windows'] = json.dumps(values['windows'])
        values['balance'] = json.dumps(values['balance']) if values['balance'] is not None else None
        values['fetched_at'] = quota_timestamp(item.fetched_at)
        values['account_fp'] = item.account_fp or None
        db.add(QuotaSnapshot(report_id=report.id, node_id=body.node, **values))
    db.flush()
    outcome = refresh.complete(db, body, report, now)
    result = {'status': 'ok', 'report_id': report.id}
    if outcome is not None:
        result['refresh_status'] = outcome
    return result


def groups(db):
    grouped = {}
    for row in db.execute(select(QuotaSnapshot).order_by(QuotaSnapshot.fetched_at.desc(), QuotaSnapshot.id.desc())).scalars():
        key = (row.provider, 'account' if row.account_fp else 'node', row.account_fp or row.node_id)
        grouped.setdefault(key, []).append(row)
    return grouped


@router.get('/api/quota')
def get_quota(compact: bool = False, principal=Depends(require_auth), db: Session = Depends(get_db, scope='function')):
    now = utcnow()
    entries = []
    for rows in groups(db).values():
        row = rows[0]
        entry = {name: getattr(row, name) for name in ('provider', 'kind', 'status', 'plan', 'account_fp', 'error')}
        entry.update(nodes=sorted({item.node_id for item in rows}), windows=windows(row),
                     balance=json.loads(row.balance) if row.balance else None,
                     fetched_at=aware(row.fetched_at).isoformat(), stale=now - aware(row.fetched_at) > dt.timedelta(hours=26))
        if row.status in ('error', 'expired'):
            last = next((item for item in rows if item.status == 'ok'), None)
            entry['last_ok'] = {'windows': windows(last), 'fetched_at': aware(last.fetched_at).isoformat()} if last else None
        entries.append(entry)
    if compact:
        by_provider = {}
        for entry in entries:
            by_provider.setdefault(entry['provider'], []).append(entry)
        entries = []
        for provider, accounts in sorted(by_provider.items()):
            # A provider's tightest known account window is a conservative
            # dispatch signal. Use latest account when no percentage exists.
            candidates = [(w['used_percent'], a, w) for a in accounts for w in a['windows'] if w['used_percent'] is not None]
            if candidates:
                _, account, window = max(candidates, key=lambda item: (item[0], item[1]['fetched_at']))
            else:
                account, window = max(accounts, key=lambda a: a['fetched_at']), None
            entries.append(dict(provider=provider, status=account['status'], window=window,
                                resets_at=window['resets_at'] if window else None,
                                fetched_at=account['fetched_at'], stale=account['stale']))
    return {'generated_at': now.isoformat(), 'providers': sorted(entries, key=lambda item: (item['provider'], item.get('account_fp') or '', item.get('nodes', []))),
            'refresh_enabled': refresh.enabled(), 'can_refresh': refresh.can_refresh(principal)}


@router.get('/api/quota/history')
def quota_history(provider: str = Query(...), days: int = Query(30, ge=1, le=90),
                  principal=Depends(require_auth), db: Session = Depends(get_db, scope='function')):
    from node.quota_probe import PROVIDERS
    if provider not in PROVIDERS:
        raise HTTPException(422, 'unknown provider')
    now = utcnow()
    start = now.replace(hour=0, minute=0, second=0, microsecond=0) - dt.timedelta(days=days - 1)
    daily = {}
    for rows in groups(db).values():
        if rows[0].provider != provider:
            continue
        for row in rows:
            stamp = aware(row.fetched_at)
            if stamp < start or stamp > now:
                continue
            for window in windows(row):
                if window['used_percent'] is None:
                    continue
                key = (stamp.date().isoformat(), row.account_fp or '', '' if row.account_fp else row.node_id, window['key'])
                daily.setdefault(key, dict(date=key[0], account_fp=row.account_fp, node=None if row.account_fp else row.node_id,
                                           key=window['key'], period=window['period'], used_percent=window['used_percent'], fetched_at=stamp.isoformat()))
    return {'generated_at': now.isoformat(), 'provider': provider, 'days': days, 'history': [daily[key] for key in sorted(daily)]}
