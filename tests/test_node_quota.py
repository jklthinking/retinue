"""Synthetic quota responses; no network, terminal, or real credentials."""
import base64
import datetime as dt
import json
from pathlib import Path
from unittest.mock import Mock, MagicMock
import urllib.error

import pytest

from node import quota_probe as q
from node import cli

FIXTURES = Path(__file__).parent / 'fixtures/quota'


def fixture(name):
    return json.loads((FIXTURES / (name + '.json')).read_text())


@pytest.mark.parametrize('provider,count', [('claude', 4), ('codex', 1), ('grok', 3), ('cursor', 5), ('kimi', 4)])
def test_samples(provider, count):
    data = fixture(provider)
    if provider == 'grok':
        windows = q.parse(provider, data['monthly'], data['credits'])
        assert {w['period'] for w in windows} == {'monthly', 'weekly'}
    elif provider == 'cursor':
        windows = q.parse(provider, data['usage'], data['sand'])
        assert windows[-1]['used_percent'] == pytest.approx(0.783434)
        assert windows[2]['used_percent'] == 98.69
    else:
        windows = q.parse(provider, data)
    assert len(windows) == count
    assert all(w['resets_at'].endswith('Z') for w in windows)


def test_generic_future_windows_and_epoch():
    data = {'unknown_alias': {'resets_at': '2026-01-01T02:00:00+02:00', 'utilization': 23},
            'extra_usage': {'daily': {'resets_at': '2026-01-02T00:00:00Z'}},
            'spend': {'resets_at': '2026-01-03T00:00:00Z'}, 'nil': None,
            'additional_rate_limits': [{'primary_window': {'reset_at': 0, 'used_percent': 0}}],
            'code_review': {'reset_at': 18000}}
    windows = q.generic_windows(data)
    assert len(windows) == 5
    assert windows[0]['resets_at'] == '2026-01-01T00:00:00Z'
    assert windows[3]['resets_at'] == '1970-01-01T00:00:00Z'


def test_kimi_relative():
    fetched = dt.datetime(2026, 1, 1, tzinfo=q.UTC)
    windows = q.parse_kimi_screen('\x1b[32m' + (FIXTURES / 'kimi_screen.txt').read_text(), fetched)
    assert windows[0]['resets_at'] == '2026-01-01T02:13:00Z'
    assert windows[1]['resets_at'] == '2026-01-04T17:13:00Z'
    assert windows[1]['period'] == 'weekly'


def test_consent_never_reads_or_executes(monkeypatch):
    monkeypatch.setattr(q, 'credential_path', Mock(side_effect=AssertionError('read')))
    monkeypatch.setattr(q, 'kimi_cli', Mock(side_effect=AssertionError('CLI')))
    monkeypatch.setattr(q, 'request', Mock(side_effect=AssertionError('network')))
    report = q.collect('sample-node', config={'enabled_providers': []})
    assert all(p['status'] == 'consent_missing' for p in report['providers'])


def test_exception_isolation_and_privacy(monkeypatch):
    def adapter(provider, cfg, result):
        if provider == 'codex':
            raise RuntimeError('private-token private-cookie private-account')
        result['windows'] = q.parse('claude', fixture('claude'))
    monkeypatch.setitem(q.REGISTRY, 'codex', adapter)
    monkeypatch.setitem(q.REGISTRY, 'claude', adapter)
    report = q.collect('sample-node', ['codex', 'claude'], {'enabled_providers': ['codex', 'claude']})
    assert [p['status'] for p in report['providers']] == ['error', 'ok']
    assert 'private' not in json.dumps(report)


def test_atomic_config_and_cli(monkeypatch, tmp_path, capsys):
    path = tmp_path / 'quota.json'
    monkeypatch.setenv('RETINUE_QUOTA_CONFIG', str(path))
    assert cli.main(['quota', '--enable', 'codex', 'grok']) == 0
    assert path.stat().st_mode & 0o777 == 0o600
    assert cli.main(['quota', '--disable', 'grok']) == 0
    assert q.load_config()['enabled_providers'] == ['codex']
    assert cli.main(['quota', '--status']) == 0
    capsys.readouterr()
    monkeypatch.setattr(q, 'probe', Mock())
    assert cli.main(['quota', '--node', 'sample-node', '--provider', 'claude', '--dry-run']) == 0
    assert json.loads(capsys.readouterr().out)['providers'][0]['status'] == 'consent_missing'


def token(payload):
    return 'header.' + base64.urlsafe_b64encode(json.dumps(payload).encode()).decode().rstrip('=') + '.signature'


@pytest.mark.parametrize('provider', ['claude', 'codex', 'grok', 'cursor', 'kimi'])
def test_mocked_adapter(provider, monkeypatch, tmp_path):
    access = token({'sub': 'issuer|sample-account', 'exp': 4102444800})
    credentials = {
        'claude': {'claudeAiOauth': {'accessToken': access, 'subscriptionType': 'team'}},
        'codex': {'tokens': {'access_token': access, 'account_id': 'sample-account'}},
        'grok': {'session': {'key': access}},
        'cursor': {'accessToken': access},
        'kimi': {'access_token': access, 'expires_at': 4102444800},
    }
    path = tmp_path / 'credentials.json'
    path.write_text(json.dumps(credentials[provider]))
    original = path.read_bytes()
    monkeypatch.setattr(q, 'credential_path', lambda _: path)
    monkeypatch.setattr(q, 'kimi_cli', Mock(side_effect=OSError()))
    data = fixture(provider)
    replies = [data]
    if provider == 'grok':
        replies = [data['monthly'], data['credits'], {'subscription_tier_display': 'SuperGrok Heavy'}]
    elif provider == 'cursor':
        replies = [data['usage'], data['sand']]
    request = Mock(side_effect=replies)
    monkeypatch.setattr(q, 'request', request)
    result = q.collect('sample-node', [provider], {'enabled_providers': [provider]})['providers'][0]
    assert result['status'] == 'ok'
    assert result['account_fp'] is not None
    assert path.read_bytes() == original
    output = json.dumps(result)
    assert access not in output and 'sample-account' not in output
    if provider == 'cursor':
        headers = request.call_args_list[0].args[1]
        assert headers['Cookie'] == 'WorkosCursorSessionToken=sample-account%3A%3A' + access
        assert request.call_args_list[1].args[-1] == 'POST'
    if provider == 'grok':
        assert result['plan'] == 'supergrok heavy'


def test_kimi_expired_no_network(monkeypatch, tmp_path):
    path = tmp_path / 'credentials.json'
    path.write_text(json.dumps({'access_token': token({'exp': 1})}))
    monkeypatch.setattr(q, 'credential_path', lambda _: path)
    monkeypatch.setattr(q, 'kimi_cli', Mock(side_effect=TimeoutError()))
    request = Mock(side_effect=AssertionError())
    monkeypatch.setattr(q, 'request', request)
    assert q.collect('sample-node', ['kimi'], {'enabled_providers': ['kimi']})['providers'][0]['status'] == 'expired'
    request.assert_not_called()


def test_moonshot_and_extensions(monkeypatch):
    monkeypatch.setenv('SAMPLE_QUOTA_KEY', 'synthetic-key')
    monkeypatch.setattr(q, 'request', Mock(return_value={'data': {'available_balance': 12.5}}))
    config = {'enabled_providers': ['moonshot', 'openai_admin'], 'providers': {'moonshot': {'key_env': 'SAMPLE_QUOTA_KEY'}}}
    report = q.collect('sample-node', ['moonshot', 'openai_admin'], config)
    assert report['providers'][0]['balance'] == {'amount': 12.5, 'currency': 'CNY'}
    assert report['providers'][1]['status'] == 'not_configured'
    assert 'synthetic-key' not in json.dumps(report)


def test_request_proxy_timeout_no_redirect(monkeypatch):
    opener = MagicMock()
    opener.open.return_value.__enter__.return_value.read.return_value = b'{"ok":true}'
    build = Mock(return_value=opener)
    monkeypatch.setattr(q.urllib.request, 'build_opener', build)
    assert q.request('https://example.invalid/quota', {'Authorization': 'Bearer synthetic'}, {'proxy': ''}) == {'ok': True}
    assert opener.open.call_args.kwargs['timeout'] == 20
    assert build.call_args.args[0].proxies == {}
    assert isinstance(build.call_args.args[1], q.NoRedirect)


def test_push_node_auth(monkeypatch):
    open_mock = Mock()
    monkeypatch.setattr(q, 'open_url', open_mock)
    payload = {'node': 'sample-node', 'collected_at': '2026-01-01T00:00:00Z', 'providers': []}
    q.push('http://127.0.0.1:9219', 'synthetic', payload)
    req = open_mock.call_args.args[0]
    assert req.full_url.endswith('/api/nodes/quota')
    assert json.loads(req.data) == payload
    assert open_mock.call_args.kwargs['request_class'] == q.RequestClass.INWARD


def test_enroll_explicit_consent(monkeypatch, tmp_path):
    monkeypatch.setenv('RETINUE_QUOTA_CONFIG', str(tmp_path / 'quota.json'))
    args = cli.build_parser().parse_args(['enroll', '--target', 'linux-user', '--quota-consent', 'codex,kimi'])
    cli._enroll_quota(args)
    assert q.load_config()['enabled_providers'] == ['codex', 'kimi']


def test_kimi_without_bwrap(monkeypatch):
    monkeypatch.setattr(q.shutil, 'which', lambda _: None)
    assert q.kimi_argv('kimi', 'workspace') == ['kimi']


@pytest.mark.parametrize('ready', [b'Welcome to Kimi Code!', '  \x1b[32m│ >'.encode(),
                                   b'\x1b[c\x1b[?996n\x1b[6nWelcome to Kimi Code!',
                                   b"Trust this folder? Don't trust"])
@pytest.mark.parametrize('activity', [None, b'Session created', b'Assistant: sample reply'])

def test_kimi_pty_usage_only(monkeypatch, tmp_path, ready, activity):
    import fcntl
    monkeypatch.setattr(fcntl, 'ioctl', Mock())
    import pty
    import select
    import subprocess
    path = tmp_path / 'credentials.json'
    path.write_text(json.dumps({'access_token': token({'exp': 1})}))
    monkeypatch.setattr(q, 'credential_path', lambda _: path)
    monkeypatch.setattr(q, 'kimi_executable', lambda: 'kimi')
    monkeypatch.setattr(q.shutil, 'which', lambda _: None)
    monkeypatch.setattr(pty, 'openpty', lambda: (101, 102))
    process = Mock(pid=42)
    process.poll.return_value = None
    process.wait.side_effect = lambda **kwargs: setattr(process.poll, 'return_value', 0)
    monkeypatch.setattr(subprocess, 'Popen', Mock(return_value=process))
    monkeypatch.setattr(select, 'select', lambda *args: ([101], [], []))
    screen = (FIXTURES / 'kimi_screen.txt').read_bytes()
    menu = '→ usage  Show session tokens + context window + plan quotas'.encode()
    read = Mock(side_effect=[ready, activity or b'Welcome to Kimi Code! /usage', menu, menu, screen])
    write = Mock()
    close = Mock()
    kill = Mock()
    monkeypatch.setattr(q.os, 'read', read)
    monkeypatch.setattr(q.os, 'write', write)
    monkeypatch.setattr(q.os, 'close', close)
    monkeypatch.setattr(q.os, 'killpg', kill)
    tick = iter(range(0, 100, 3))
    monkeypatch.setattr(q.time, 'monotonic', lambda: next(tick))
    if activity:
        with pytest.raises(q.KimiSessionError):
            q.kimi_cli()
    else:
        assert len(q.kimi_cli()) == 2
    writes = [call.args[1] for call in write.call_args_list]
    prefix = [b'\x1b[?1;2c', b'\x1b[?997;1n', b'\x1b[1;1R'] if b'\x1b[c' in ready else []
    if b'Trust this folder?' in ready:
        prefix.append(b'\r')
    expected = [] if activity and b'Trust this folder?' in ready else [b'/usage']
    assert writes == prefix + expected + ([] if activity else [b'\r', b'\r'])
    close.assert_any_call(101)
    close.assert_any_call(102)
    kill.assert_called_once()
    process.wait.assert_called_once()


def test_kimi_pty_timeout_kills(monkeypatch, tmp_path):
    import fcntl
    monkeypatch.setattr(fcntl, 'ioctl', Mock())
    import pty
    import select
    import subprocess
    path = tmp_path / 'credentials.json'
    path.write_text(json.dumps({'access_token': token({'exp': 1})}))
    monkeypatch.setattr(q, 'credential_path', lambda _: path)
    monkeypatch.setattr(q, 'kimi_executable', lambda: 'kimi')
    monkeypatch.setattr(q.shutil, 'which', lambda _: None)
    monkeypatch.setattr(pty, 'openpty', lambda: (101, 102))
    monkeypatch.setattr(select, 'select', lambda *args: ([], [], []))
    process = Mock(pid=42)
    process.poll.return_value = None
    process.wait.side_effect = [subprocess.TimeoutExpired('kimi', 1), subprocess.TimeoutExpired('kimi', 1), 0]
    monkeypatch.setattr(subprocess, 'Popen', Mock(return_value=process))
    kill = Mock()
    monkeypatch.setattr(q.os, 'killpg', kill)
    monkeypatch.setattr(q.os, 'close', Mock())
    tick = iter([0, 56])
    monkeypatch.setattr(q.time, 'monotonic', lambda: next(tick))
    with pytest.raises(TimeoutError):
        q.kimi_cli()
    assert kill.call_count == 3


def test_kimi_cli_success_skips_api(monkeypatch):
    windows = q.parse_kimi_screen((FIXTURES / 'kimi_screen.txt').read_text(), q.now())
    monkeypatch.setattr(q, 'kimi_cli', Mock(return_value=windows))
    request = Mock()
    monkeypatch.setattr(q, 'request', request)
    result = q.collect('sample-node', ['kimi'], {'enabled_providers': ['kimi']})['providers'][0]
    assert result['source'] == 'cli' and result['windows'] == windows
    request.assert_not_called()


def test_kimi_fallback_reads_renewed_credentials(monkeypatch, tmp_path):
    path = tmp_path / 'credentials.json'
    path.write_text(json.dumps({'access_token': token({'exp': 1})}))
    monkeypatch.setattr(q, 'credential_path', lambda _: path)

    def failed_cli(config):
        path.write_text(json.dumps({'access_token': token({'exp': 4102444800})}))
        raise ValueError('Unparseable quota UI')

    monkeypatch.setattr(q, 'kimi_cli', failed_cli)
    request = Mock(return_value=fixture('kimi'))
    monkeypatch.setattr(q, 'request', request)
    result = q.collect('sample-node', ['kimi'], {'enabled_providers': ['kimi']})['providers'][0]
    assert result['status'] == 'ok' and result['source'] == 'api'
    request.assert_called_once()


def test_kimi_session_activity_has_no_api_fallback(monkeypatch):
    monkeypatch.setattr(q, 'kimi_cli', Mock(side_effect=q.KimiSessionError()))
    credentials = Mock(side_effect=AssertionError('Credential read'))
    monkeypatch.setattr(q, 'credential_path', credentials)
    result = q.collect('sample-node', ['kimi'], {'enabled_providers': ['kimi']})['providers'][0]
    assert result['status'] == 'error'
    credentials.assert_not_called()


def test_cursor_grok_bot_usage_percent_is_already_a_percentage():
    import json
    from pathlib import Path
    from node import quota_probe
    data = json.loads((Path(__file__).parent / "fixtures" / "quota" / "cursor.json").read_text(encoding="utf-8"))
    windows = quota_probe.parse("cursor", data["usage"], data["sand"])
    bot = [w for w in windows if w["key"] == "weekly.grok_bot"]
    assert bot and abs(bot[0]["used_percent"] - 0.783434) < 1e-9


def test_collect_deduplicates_claude_windows(monkeypatch):
    def probe(provider, config, result):
        result['windows'] = q.parse(provider, fixture('claude'))
    monkeypatch.setitem(q.REGISTRY, 'claude', probe)
    result = q.collect('sample-node', ['claude'], {'enabled_providers': ['claude']})
    windows = result['providers'][0]['windows']
    assert len(windows) == 2
    assert {w['key'] for w in windows} == {'five_hour', 'seven_day'}


def test_collect_reports_only_consented_providers_by_default(monkeypatch):
    monkeypatch.setitem(q.REGISTRY, "codex", lambda provider, cfg, result: result["windows"].append(
        {"key": "w", "label": "w", "period": "weekly", "used_percent": 1.0, "used": None, "limit": None,
         "unit": "percent", "resets_at": "2026-10-10T00:00:00Z", "raw_reset": None}))
    payload = q.collect("sample-node", config={"enabled_providers": ["codex"], "providers": {}})
    assert [item["provider"] for item in payload["providers"]] == ["codex"]
    explicit = q.collect("sample-node", ["claude"], config={"enabled_providers": ["codex"], "providers": {}})
    assert explicit["providers"][0]["status"] == "consent_missing"


def test_grok_product_windows_are_named_and_empty_products_skipped():
    data = fixture('grok')
    credits = json.loads(json.dumps(data['credits']))
    config = credits.get('config', credits)
    config['productUsage'] = [{'product': 'GrokBuild', 'usagePercent': 40.0}, {'product': 'GrokChat'}]
    windows = q.parse('grok', data['monthly'], credits)
    products = [w for w in windows if w['key'].startswith('weekly.product.')]
    assert [(w['key'], w['label'], w['used_percent']) for w in products] == [('weekly.product.GrokBuild', 'GrokBuild 每周', 40.0)]


def test_reenroll_preserves_consent_and_proxy(monkeypatch):
    original = {'enabled_providers':['codex'], 'consented_at':'2026-01-01T00:00:00Z',
                'providers':{'codex':{'proxy':''}}}
    q.save_config(original)
    monkeypatch.setattr('sys.stdin.isatty', lambda: False)
    args = cli.build_parser().parse_args(['enroll','--target','linux-user','--install'])
    cli._enroll_quota(args)
    assert q.load_config() == original
    args.quota_consent = 'none'
    cli._enroll_quota(args)
    assert q.load_config()['enabled_providers'] == []
    assert q.load_config()['providers'] == original['providers']


def test_quota_poll_shares_lock_and_preserves_consent(monkeypatch):
    from node import quota_refresh as r
    q.save_config({'enabled_providers':[]})
    claim = Mock(return_value={'id':'a'*32,'providers':['grok'],
                'deadline_in':180, 'deadline':q.iso(q.now()+dt.timedelta(minutes=3))})
    monkeypatch.setattr(r, 'claim', claim)
    monkeypatch.setattr(q, 'credential_path', Mock(side_effect=AssertionError('credential read')))
    monkeypatch.setattr(q, 'kimi_cli', Mock(side_effect=AssertionError('CLI')))
    push = Mock(); monkeypatch.setattr(q, 'push', push)
    with r.collection_lock() as acquired:
        assert acquired
        assert r.poll('http://127.0.0.1:9219','synthetic','sample-node') == 'busy'
        claim.assert_not_called()
    assert r.poll('http://127.0.0.1:9219','synthetic','sample-node') == 'reported'
    body = push.call_args.args[2]
    assert body['refresh_request_id'] == 'a'*32
    assert body['providers'][0]['status'] == 'consent_missing'
    assert dt.datetime.fromisoformat(body['collected_at'].replace('Z','+00:00')) >= q.now()-dt.timedelta(seconds=2)


def test_quota_deadline_retains_successful_providers(monkeypatch):
    from node import quota_refresh as r
    def adapter(provider, config, result):
        if provider == 'grok': raise r.CollectionDeadline()
        result['windows'] = q.parse('claude', fixture('claude'))
    monkeypatch.setitem(q.REGISTRY,'claude', adapter); monkeypatch.setitem(q.REGISTRY,'grok', adapter)
    body = r.bounded_collect('sample-node',['claude','grok'],{'enabled_providers':['claude','grok']})
    assert [row['status'] for row in body['providers']] == ['ok','error']


@pytest.mark.parametrize('mutation', [lambda x:x.update(command='whoami'), lambda x:x.update(providers=['unknown']),
                                     lambda x:x.update(type='execute'), lambda x:x.update(id='../path')])
def test_quota_claim_rejects_executable_input(monkeypatch, mutation):
    from node import quota_refresh as r
    body = {'id':'a'*32,'type':'quota_refresh','providers':['grok'],
            'deadline_in':180, 'deadline':q.iso(q.now()+dt.timedelta(minutes=3))}
    mutation(body)
    response = MagicMock(); response.__enter__.return_value = response
    response.read.return_value = json.dumps(body).encode(); response.status = 200
    monkeypatch.setattr(r, 'open_url', Mock(return_value=response))
    with pytest.raises(ValueError): r.claim('http://127.0.0.1:9219','synthetic','sample-node')
