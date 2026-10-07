"""Synthetic quota ingestion, identity scope, projections and migration."""
import datetime as dt
import sqlite3

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from server.app import create_app
from server.db import Actor, ApiToken, Node, NodeToken, QuotaReport, QuotaSnapshot, make_session_factory, migrate_database, utcnow
from server.security import hash_token


@pytest.fixture
def setup(tmp_path):
    factory = make_session_factory(tmp_path / 'test.db')
    with factory() as db:
        db.add(Actor(id='reader', kind='agent'))
        db.add_all([Node(id=n, membership_status='admitted') for n in ('sample-a', 'sample-b')])
        db.flush()
        db.add(ApiToken(actor_id='reader', token_hash=hash_token('synthetic-reader')))
        db.add_all([NodeToken(node_id=n, token_hash=hash_token(n)) for n in ('sample-a', 'sample-b')])
        db.commit()
    return TestClient(create_app(factory, data_dir=tmp_path)), factory


def headers(node='sample-a'):
    return {'Authorization': 'Bearer ' + node}


def payload(node='sample-a', stamp=None, status='ok', percent=42):
    stamp = stamp or utcnow().isoformat()
    return dict(node=node, collected_at=stamp, providers=[dict(
        provider='claude', kind='subscription', status=status, plan='pro', account_fp='abcdef123456', source='api',
        windows=[dict(key='five_hour', label='five_hour', period='5h', used_percent=percent,
                      used=None, limit=None, unit='percent', resets_at='2027-01-01T00:00:00Z', raw_reset=None)],
        balance=None, error='Quota query failed' if status == 'error' else None, fetched_at=stamp)])


def post(client, body):
    return client.post('/api/nodes/quota', json=body, headers=headers(body['node']))


def read(client, path='/api/quota'):
    return client.get(path, headers=headers('synthetic-reader'))


def test_auth_and_validation(setup):
    client, factory = setup
    assert client.post('/api/nodes/quota', json=payload()).status_code == 401
    assert client.post('/api/nodes/quota', json=payload('sample-b'), headers=headers()).status_code == 403
    assert client.post('/api/nodes/quota', json=payload(), headers=headers('synthetic-reader')).status_code == 403
    assert client.get('/api/quota').status_code == 401
    mutations = [lambda b: b.update(access_token='synthetic'),
                 lambda b: b['providers'][0].update(cookie='synthetic'),
                 lambda b: b['providers'][0]['windows'][0].update(secret='synthetic'),
                 lambda b: b['providers'][0].update(balance={'amount': 1, 'currency': 'USD', 'token': 'synthetic'}),
                 lambda b: b['providers'][0].update(provider='unknown'),
                 lambda b: b['providers'][0].update(status='unknown'),
                 lambda b: b['providers'][0].update(error='Authorization: Bearer synthetic'),
                 lambda b: b['providers'][0].update(plan='x'*65),
                 lambda b: b['providers'][0]['windows'][0].update(resets_at='tomorrow'),
                 lambda b: b['providers'][0]['windows'][0].update(used_percent=101),
                 lambda b: b['providers'][0].update(windows=b['providers'][0]['windows']*51)]
    for mutate in mutations:
        body = payload()
        mutate(body)
        assert post(client, body).status_code == 422
    assert client.post('/api/nodes/quota', content=b' '*262145, headers=headers()).status_code == 413
    with factory() as db:
        assert not list(db.execute(select(QuotaReport)).scalars())


def test_merge_last_ok_stale_history_compact_and_retention(setup):
    client, factory = setup
    # Anchor all samples inside one UTC day (36h+ ago) so daily history
    # buckets never straddle midnight, whatever time the suite runs.
    old = (utcnow() - dt.timedelta(days=2)).replace(hour=12, minute=0, second=0, microsecond=0)
    assert post(client, payload(stamp=(old-dt.timedelta(hours=1)).isoformat())).status_code == 200
    assert post(client, payload('sample-b', old.isoformat(), 'error', 81)).status_code == 200
    # Late delivery does not overwrite a more recently fetched snapshot.
    assert post(client, payload(stamp=(old-dt.timedelta(hours=2)).isoformat(), percent=3)).status_code == 200
    rows = read(client).json()['providers']
    assert len(rows) == 1
    row = rows[0]
    assert row['nodes'] == ['sample-a', 'sample-b']
    assert row['status'] == 'error' and row['stale']
    assert row['last_ok']['windows'][0]['used_percent'] == 42
    assert read(client, '/api/quota?compact=1').json()['providers'][0]['window']['used_percent'] == 81
    history = read(client, '/api/quota/history?provider=claude&days=30').json()['history']
    assert len(history) == 1 and history[0]['used_percent'] == 81
    assert read(client, '/api/quota/history?provider=unknown').status_code == 422
    assert read(client, '/api/quota/history?provider=claude&days=91').status_code == 422
    for node in ('sample-a', 'sample-b'):
        body = payload(node)
        body['providers'][0]['account_fp'] = None
        assert post(client, body).status_code == 200
    assert len(read(client).json()['providers']) == 3
    assert len(read(client, '/api/quota?compact=1').json()['providers']) == 1
    with factory() as db:
        report = db.execute(select(QuotaReport).order_by(QuotaReport.id)).scalars().first()
        expired_id = report.id
        report.received_at = utcnow() - dt.timedelta(days=91)
        db.commit()
    assert post(client, payload()).status_code == 200
    with factory() as db:
        assert db.get(QuotaReport, expired_id) is None
        assert not list(db.execute(select(QuotaSnapshot).where(QuotaSnapshot.report_id == expired_id)).scalars())


def test_upgrade_25_only_adds_tables(tmp_path):
    path = tmp_path / 'test.db'
    make_session_factory(path)
    with sqlite3.connect(path) as db:
        db.execute('DROP TABLE quota_snapshots')
        db.execute('DROP TABLE quota_reports')
        db.execute('UPDATE schema_version SET version=25')
        before = db.execute("SELECT name, sql FROM sqlite_master WHERE type='table'").fetchall()
    migrate_database(path)
    with sqlite3.connect(path) as db:
        assert db.execute('SELECT version FROM schema_version').fetchone()[0] == 26
        after = dict(db.execute("SELECT name, sql FROM sqlite_master WHERE type='table'").fetchall())
        assert all(after[name] == ddl for name, ddl in before)
        assert {'quota_reports', 'quota_snapshots'} <= after.keys()
        assert db.execute("SELECT name FROM sqlite_master WHERE name='ix_quota_account_fetched'").fetchone()
    migrate_database(path)


def test_expired_last_ok_sorted_windows_and_login_read(setup):
    from server.db import User
    from server.security import hash_password
    client, factory = setup
    with factory() as db:
        db.add(User(username='sample-user', password_hash=hash_password('synthetic-pass'), role='viewer'))
        db.commit()
    body = payload()
    early = dict(body['providers'][0]['windows'][0], key='seven_day', resets_at='2026-12-01T00:00:00Z')
    body['providers'][0]['windows'].append(early)
    assert post(client, body).status_code == 200
    row = read(client).json()['providers'][0]
    assert row['windows'][0]['key'] == 'seven_day' and not row['stale']
    body = payload(status='expired')
    body['providers'][0]['windows'] = []
    assert post(client, body).status_code == 200
    row = read(client).json()['providers'][0]
    assert row['status'] == 'expired' and len(row['last_ok']['windows']) == 2
    assert client.post('/api/auth/login', json={'username':'sample-user', 'password':'synthetic-pass'}).status_code == 200
    assert client.get('/api/quota').status_code == 200
    assert client.get('/api/quota/history?provider=claude').status_code == 200
