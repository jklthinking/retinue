"""Synthetic quota ingestion, identity scope, projections and migration."""
import datetime as dt
import sqlite3

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from server.app import create_app
from server.db import Actor, ApiToken, Node, NodeToken, QuotaReport, QuotaSnapshot, QuotaRefreshRequest, QuotaRefreshBatch, make_session_factory, migrate_database, utcnow
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
        assert db.execute('SELECT version FROM schema_version').fetchone()[0] == 27
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


def operator(client, factory, role='admin'):
    from server.db import User
    from server.security import hash_password
    with factory() as db:
        db.add(User(username='sample-operator', password_hash=hash_password('synthetic-pass'), role=role))
        db.commit()
    assert client.post('/api/auth/login', json={'username':'sample-operator', 'password':'synthetic-pass'}).status_code == 200


def start(client, **scope):
    return client.post('/api/quota/refresh', json=scope)


def claim(client, node='sample-a'):
    return client.post('/api/nodes/quota/refresh/claim', json={'node': node}, headers=headers(node))


def status(client, batch):
    return client.get('/api/quota/refresh/' + batch['batch_id']).json()


def test_refresh_fresh_report_required_and_terminal_immutable(setup):
    client, factory = setup
    assert post(client, payload()).status_code == 200
    operator(client, factory)
    batch = start(client).json()
    request = claim(client).json()
    assert batch['requests'][0]['status'] == 'queued'
    assert claim(client).status_code == 204
    assert post(client, payload()).status_code == 200  # Ordinary reports are not receipts.
    assert status(client, batch)['requests'][0]['status'] == 'claimed'
    old = payload(stamp=(utcnow()-dt.timedelta(hours=1)).isoformat())
    old['refresh_request_id'] = request['id']
    assert post(client, old).json()['refresh_status'] == 'ignored'
    fresh = payload(percent=71)
    fresh['refresh_request_id'] = request['id']
    assert post(client, fresh).json()['refresh_status'] == 'done'
    result = status(client, batch)['requests'][0]
    assert result['status'] == 'done' and result['fetched_at'] == fresh['collected_at']
    assert result['results'] == [{'provider':'claude', 'status':'ok'}]
    assert 'account_fp' not in str(result)
    fresh['providers'][0]['status'] = 'error'
    assert post(client, fresh).json()['refresh_status'] == 'ignored'
    assert status(client, batch)['requests'][0]['status'] == 'done'


def test_refresh_partial_failed_and_scope_cannot_expand(setup):
    client, factory = setup
    post(client, payload())
    operator(client, factory)
    batch = start(client, providers=['claude','grok']).json()
    assert start(client, providers=['codex']).status_code == 409
    request = claim(client).json()
    fresh = payload()
    fresh['refresh_request_id'] = request['id']
    assert post(client, fresh).json()['refresh_status'] == 'partial'
    assert status(client, batch)['requests'][0]['results'][-1] == {'provider':'grok','status':'consent_missing'}
    post(client, payload('sample-b'))
    failed = start(client, nodes=['sample-b']).json()
    request = claim(client, 'sample-b').json()
    fresh = payload('sample-b', status='error')
    fresh['refresh_request_id'] = request['id']
    assert post(client, fresh).json()['refresh_status'] == 'failed'
    assert status(client, failed)['requests'][0]['status'] == 'failed'


def test_refresh_dedup_idempotency_and_batches_immutable(setup):
    client, factory = setup
    post(client, payload())
    operator(client, factory)
    first = start(client, request_key='a'*32).json()
    second = start(client, request_key='b'*32).json()
    assert first['batch_id'] != second['batch_id']
    assert first['requests'][0]['id'] == second['requests'][0]['id']
    assert second['requests'][0]['deduplicated']
    assert start(client, request_key='a'*32).json()['batch_id'] == first['batch_id']
    assert start(client, request_key='a'*32, providers=['grok']).status_code == 409
    with factory() as db:
        original = db.get(QuotaRefreshBatch, first['batch_id']).entries_json
        assert len(list(db.scalars(select(QuotaRefreshRequest)))) == 1
    request = claim(client).json()
    body = payload(); body['refresh_request_id'] = request['id']
    post(client, body)
    assert start(client).status_code == 429
    assert status(client, second)['requests'][0]['status'] == 'done'
    with factory() as db:
        assert db.get(QuotaRefreshBatch, first['batch_id']).entries_json == original


@pytest.mark.parametrize('claimed', [False, True])
def test_refresh_expiry_and_late_report(setup, claimed):
    client, factory = setup
    post(client, payload()); operator(client, factory)
    batch = start(client).json()
    request_id = batch['requests'][0]['id']
    if claimed: claim(client)
    with factory() as db:
        db.get(QuotaRefreshRequest, request_id).expires_at = utcnow()-dt.timedelta(seconds=1)
        db.commit()
    assert status(client, batch)['requests'][0]['status'] == 'timeout'
    body = payload(); body['refresh_request_id'] = request_id
    assert post(client, body).json()['refresh_status'] == 'ignored'
    assert claim(client).status_code == 204


@pytest.mark.parametrize('role', ['viewer', 'member'])
def test_refresh_user_roles(setup, monkeypatch, role):
    client, factory = setup
    post(client, payload()); operator(client, factory, role)
    assert start(client).status_code == 403
    assert client.get('/api/quota').json()['can_refresh'] is False
    if role == 'member':
        monkeypatch.setenv('RETINUE_QUOTA_REFRESH_MEMBERS', '1')
        assert start(client).status_code == 200


def test_refresh_anonymous_agent_node_scope_and_validation(setup):
    client, factory = setup
    assert start(client).status_code == 401
    assert client.post('/api/quota/refresh', json={}, headers=headers()).status_code == 403
    assert client.post('/api/quota/refresh', json={}, headers=headers('synthetic-reader')).status_code == 403
    assert claim(client).status_code == 204
    assert client.post('/api/nodes/quota/refresh/claim', json={'node':'sample-b'}, headers=headers()).status_code == 403
    assert client.post('/api/nodes/quota/refresh/claim', json={'node':'sample-a'}, headers=headers('synthetic-reader')).status_code == 403
    post(client, payload()); post(client, payload('sample-b')); operator(client, factory)
    for body in [{'command':'whoami'}, {'argv':[]}, {'url':'https://example.invalid'}, {'providers':['unknown']}, {'nodes':['../x']}, {'providers':['grok','grok']}]:
        assert start(client, **body).status_code == 422
    assert client.post('/api/quota/refresh', content=b' '*16385).status_code == 413
    batch = start(client, nodes=['sample-b']).json()
    assert claim(client).status_code == 204
    request = claim(client, 'sample-b').json()
    body = payload(); body['refresh_request_id'] = request['id']
    assert post(client, body).json()['refresh_status'] == 'ignored'
    assert status(client, batch)['requests'][0]['status'] == 'claimed'


def test_refresh_daily_limit_feature_flag_and_unique_constraint(setup, monkeypatch):
    from sqlalchemy.exc import IntegrityError
    client, factory = setup
    post(client, payload()); operator(client, factory)
    batch = start(client).json()
    with factory() as db:
        row = db.get(QuotaRefreshRequest, batch['requests'][0]['id'])
        db.add(QuotaRefreshRequest(id='f'*32, node_id=row.node_id, requested_by='sample', status='queued',
                                  created_at=utcnow(), expires_at=utcnow()+dt.timedelta(minutes=1)))
        with pytest.raises(IntegrityError): db.commit()
    with factory() as db:
        row = db.get(QuotaRefreshRequest, batch['requests'][0]['id'])
        row.status = 'done'; row.completed_at = utcnow()-dt.timedelta(minutes=5)
        db.commit()
    monkeypatch.setattr('server.quota_refresh.DAILY_LIMIT', 1)
    assert start(client).status_code == 429
    monkeypatch.setenv('RETINUE_QUOTA_REFRESH', '0')
    assert start(client).status_code == 404
    assert claim(client).status_code == 404
    assert client.get('/api/quota').json()['refresh_enabled'] is False


def test_upgrade_26_preserves_old_tables_and_data(tmp_path):
    path = tmp_path / 'test.db'
    make_session_factory(path)
    with sqlite3.connect(path) as db:
        db.execute('DROP TABLE quota_refresh_batches'); db.execute('DROP TABLE quota_refresh_requests')
        db.execute('UPDATE schema_version SET version=26')
        before = dict(db.execute("SELECT name, sql FROM sqlite_master WHERE type='table'"))
    migrate_database(path)
    with sqlite3.connect(path) as db:
        assert db.execute('SELECT version FROM schema_version').fetchone()[0] == 27
        after = dict(db.execute("SELECT name, sql FROM sqlite_master WHERE type='table'"))
        assert all(after[name] == ddl for name, ddl in before.items())
        assert db.execute("SELECT name FROM sqlite_master WHERE name='ux_quota_refresh_active_node'").fetchone()


@pytest.mark.parametrize('offset,expected', [(-30,'done'),(-121,'ignored'),(121,'ignored')])
def test_refresh_clock_skew_and_server_receipt(setup, offset, expected):
    client, factory = setup
    post(client, payload()); operator(client, factory)
    batch = start(client).json(); request = claim(client).json()
    assert request['deadline_in'] == 180
    assert (dt.datetime.fromisoformat(request['deadline'])-utcnow()).total_seconds() > 175
    body = payload(stamp=(utcnow()+dt.timedelta(seconds=offset)).isoformat())
    body['refresh_request_id'] = request['id']
    assert post(client, body).json()['refresh_status'] == expected
    if expected == 'done':
        result = status(client, batch)['requests'][0]
        assert result['received_at'] >= batch['created_at']


def test_refresh_index_compiles_for_postgresql():
    from sqlalchemy.dialects import postgresql
    from sqlalchemy.schema import CreateIndex
    index = next(i for i in QuotaRefreshRequest.__table__.indexes if i.name == 'ux_quota_refresh_active_node')
    ddl = str(CreateIndex(index).compile(dialect=postgresql.dialect()))
    assert 'UNIQUE' in ddl and "WHERE status IN ('queued', 'claimed')" in ddl
