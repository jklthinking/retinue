"""Consent-gated, read-only quota adapters. Credentials never enter reports."""
from __future__ import annotations

import base64
import datetime as dt
import hashlib
import json
import math
import os
from pathlib import Path
import re
import shutil
import tempfile
import time
import urllib.error
import urllib.request

from .http_client import RequestClass, open_url

UTC = dt.timezone.utc
PROVIDERS = ('claude', 'codex', 'grok', 'cursor', 'kimi', 'moonshot',
             'xai_management', 'anthropic_admin', 'openai_admin', 'cursor_admin')


def now():
    return dt.datetime.now(UTC)


def iso(value):
    return value.astimezone(UTC).isoformat().replace('+00:00', 'Z')


def config_file():
    from .runtime_pins import default_pins_file
    return Path(os.environ['RETINUE_QUOTA_CONFIG']).expanduser() if os.environ.get('RETINUE_QUOTA_CONFIG') else default_pins_file(Path.home(), os.name == 'nt').with_name('quota.json')


def load_config():
    try:
        data = json.loads(config_file().read_text(encoding='utf-8'))
        if not isinstance(data, dict) or not isinstance(data.get('enabled_providers', []), list):
            raise ValueError()
        selection(data.get('enabled_providers', []))
        return data
    except FileNotFoundError:
        return {'enabled_providers': []}
    except Exception:
        raise SystemExit('额度配置无效或不可读') from None


def selection(values):
    items = [p for value in values for p in value.split(',') if p]
    if any(p not in PROVIDERS for p in items):
        raise ValueError('未知额度 provider')
    return list(dict.fromkeys(items))


def save_config(data):
    path = config_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(dir=path.parent, prefix='.quota-')
    try:
        os.chmod(name, 0o600)
        with os.fdopen(fd, 'w', encoding='utf-8') as stream:
            json.dump(data, stream, ensure_ascii=False, indent=2)
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def credential_path(provider):
    home = Path.home()
    paths = {
        'claude': home / '.claude/.credentials.json',
        'codex': Path(os.environ.get('CODEX_HOME', str(home / '.codex'))) / 'auth.json',
        'grok': Path(os.environ.get('GROK_HOME', str(home / '.grok'))) / 'auth.json',
        'cursor': home / '.config/cursor/auth.json',
        'kimi': home / '.kimi-code/credentials/kimi-code.json',
    }
    return paths[provider].expanduser()


def kimi_executable():
    from . import runtime_pins, runtime_probe
    pin = runtime_pins.load().runtimes.get('kimi')
    if pin:
        return pin
    for command in ('kimi', 'kimi-cli'):
        found = shutil.which(command)
        if found:
            return found
        for directory in [*runtime_probe._search_directories(), Path.home() / '.kimi-code/bin']:
            found = shutil.which(command, path=str(directory))
            if found:
                return found
    return None


def detected():
    return [p for p in PROVIDERS[:5] if credential_path(p).is_file() or (p == 'kimi' and kimi_executable())]


def number(value):
    if isinstance(value, dict):
        value = value.get('val')
    if isinstance(value, bool):
        return None
    try:
        result = float(value)
        return result if math.isfinite(result) else None
    except (ValueError, TypeError):
        return None


def reset(value):
    if isinstance(value, (float, int)):
        return iso(dt.datetime.fromtimestamp(value, UTC))
    if isinstance(value, str):
        parsed = dt.datetime.fromisoformat(value.replace('Z', '+00:00'))
        if parsed.tzinfo is None:
            raise ValueError('reset requires timezone')
        return iso(parsed)
    return None


def period(key, obj):
    seconds = number(obj.get('limit_window_seconds'))
    if seconds:
        return {18000: '5h', 604800: 'weekly', 86400: 'daily'}.get(seconds, 'other')
    text = key.lower() + str(obj.get('kind', '')).lower()
    for terms, label in [(('five_hour', '5h', 'session'), '5h'), (('seven_day', '7d', 'week'), 'weekly'), (('month',), 'monthly'), (('daily',), 'daily')]:
        if any(term in text for term in terms):
            return label
    return 'other'


def window(key, obj, raw, cadence=None, percent=None):
    used = number(obj.get('used', obj.get('used_dollars', obj.get('used_credits'))))
    limit = number(obj.get('limit', obj.get('limit_dollars', obj.get('monthlyLimit'))))
    remaining = number(obj.get('remaining'))
    if used is None and limit is not None and remaining is not None:
        used = limit - remaining
    if percent is None:
        for field in ('utilization', 'used_percent', 'percent', 'usagePercent', 'creditUsagePercent'):
            percent = number(obj.get(field))
            if percent is not None:
                break
        if percent is None and number(obj.get('used_ratio')) is not None:
            percent = number(obj['used_ratio']) * 100
        if percent is None and used is not None and limit:
            percent = used / limit * 100
    return dict(key=key, label=key, period=cadence or period(key, obj), used_percent=percent,
                used=used, limit=limit, unit='percent' if percent is not None and used is None else 'other',
                resets_at=reset(raw), raw_reset=raw)


def generic_windows(data, prefix=''):
    result = []
    if isinstance(data, dict):
        raw = next((data[k] for k in ('resets_at', 'reset_at', 'resetTime', 'reset_time') if data.get(k) is not None), None)
        if raw is not None:
            result.append(window(prefix or 'usage', data, raw))
        for index, (key, value) in enumerate(data.items()):
            # Preserve schema aliases; opaque/non-schema keys get neutral labels.
            label = key if re.fullmatch(r'[a-zA-Z][a-zA-Z0-9_-]{0,63}', key) else f'field_{index}'
            result.extend(generic_windows(value, f'{prefix}.{label}'.strip('.')))
    elif isinstance(data, list):
        for index, value in enumerate(data):
            result.extend(generic_windows(value, f'{prefix}.{index}'))
    return result


def parse(provider, data, extra=None):
    if provider in ('claude', 'codex', 'kimi'):
        result = generic_windows(data)
        if provider == 'kimi':
            for item in result:
                if item['key'] == 'usage':
                    item['period'] = 'weekly'
                elif item['key'].startswith('limits.'):
                    item['period'] = '5h'
        return result
    if provider == 'grok':
        monthly, credits = data.get('config', data), extra.get('config', extra)
        result = [window('monthly', monthly, monthly.get('billingPeriodEnd'), 'monthly'),
                  window('weekly', credits, credits.get('billingPeriodEnd') or credits.get('currentPeriod', {}).get('end'), 'weekly')]
        for index, product in enumerate(credits.get('productUsage', [])):
            # Products without a usage figure (e.g. an unused GrokChat entry) carry no signal.
            if number(product.get('usagePercent')) is None:
                continue
            name = re.sub(r'[^A-Za-z0-9]', '', str(product.get('product') or ''))[:32] or str(index)
            item = window(f'weekly.product.{name}', product, result[1]['raw_reset'], 'weekly')
            item['label'] = name + ' 每周'
            result.append(item)
        return result
    if provider == 'cursor':
        usage = data.get('individualUsage', {})
        plan = usage.get('plan', {})
        raw = data.get('billingCycleEnd')
        result = [window('monthly.plan', plan, raw, 'monthly', number(plan.get('totalPercentUsed')))]
        for key in ('auto', 'api'):
            result.append(window('monthly.' + key, {}, raw, 'monthly', number(plan.get(key + 'PercentUsed'))))
        result.append(window('monthly.on_demand', usage.get('onDemand', {}), raw, 'monthly'))
        result.append(window('weekly.grok_bot', extra, extra.get('nextResetTimestampUtc'), 'weekly', number(extra.get('usagePercent'))))  # usagePercent is already a percentage
        return result
    return []


def jwt(token):
    try:
        body = token.split('.')[1]
        return json.loads(base64.urlsafe_b64decode(body + '=' * (-len(body) % 4)))
    except Exception:
        return {}


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        # Never forward authentication to a login or third-party redirect.
        return None


def request(url, headers, config, method='GET'):
    proxy = config.get('proxy')
    handler = urllib.request.ProxyHandler() if proxy is None else urllib.request.ProxyHandler({'https': proxy} if proxy else {})
    opener = urllib.request.build_opener(handler, NoRedirect())
    req = urllib.request.Request(url, headers={'Accept': 'application/json', **headers}, method=method,
                                 data=b'{}' if method == 'POST' else None)
    with opener.open(req, timeout=20) as response:
        return json.load(response)


def parse_kimi_screen(screen, fetched):
    screen = re.sub(r'\x1b\[[0-?]*[ -/]*[@-~]', '', screen)
    block = screen.rsplit('Plan usage', 1)[-1] if 'Plan usage' in screen else ''
    result = []
    for label, percent, duration in re.findall(r'(5h limit|Weekly limit)[^\r\n]*?(\d+(?:\.\d+)?)%\s*used\s*resets in\s*((?:\d+\s*[dhms]\s*)+)', block):
        seconds = sum(int(n) * {'d': 86400, 'h': 3600, 'm': 60, 's': 1}[unit] for n, unit in re.findall(r'(\d+)\s*([dhms])', duration))
        item = window('5h' if label == '5h limit' else 'weekly', {}, iso(fetched + dt.timedelta(seconds=seconds)), percent=float(percent))
        item['raw_reset'] = duration.strip()
        result.append(item)
    return result


class KimiSessionError(RuntimeError):
    """The quota UI unexpectedly entered a model session."""


def kimi_argv(executable, directory):
    # The CLI owns credential renewal; Retinue never writes credentials itself.
    return [executable]


def kimi_cli(config=None):
    """Run only the built-in usage command, in an empty disposable workspace."""
    import fcntl
    import struct
    import termios
    import pty
    import select
    import signal
    import subprocess
    executable = kimi_executable()
    if not executable:
        raise FileNotFoundError()
    master, slave = pty.openpty()
    process = None
    deadline = time.monotonic() + 55  # reserve five seconds for shutdown
    try:
        fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack('HHHH', 40, 160, 0, 0))
        with tempfile.TemporaryDirectory(prefix='retinue-quota-') as directory:
            environment = {**os.environ, 'TERM': 'xterm-256color'}
            if config is not None and 'proxy' in config:
                for key in ('HTTPS_PROXY', 'https_proxy', 'HTTP_PROXY', 'http_proxy', 'ALL_PROXY', 'all_proxy'):
                    environment.pop(key, None)
                if config['proxy']:
                    environment['HTTPS_PROXY'] = config['proxy']
            process = subprocess.Popen(kimi_argv(executable, directory), stdin=slave, stdout=slave, stderr=slave,
                                       cwd=directory, start_new_session=True,
                                       env=environment)
            os.close(slave)
            slave = None
            output = ''
            workspace_confirmed = False
            sent = False
            enters = 0
            sent_at = 0
            while time.monotonic() < deadline and process.poll() is None:
                if select.select([master], [], [], .2)[0]:
                    chunk = os.read(master, 65536).decode('utf-8', errors='replace')
                    # Answer terminal capability/theme queries, never model input.
                    if '\x1b[c' in chunk:
                        os.write(master, b'\x1b[?1;2c')
                    if '\x1b[?996n' in chunk:
                        os.write(master, b'\x1b[?997;1n')
                    if '\x1b[6n' in chunk:
                        os.write(master, b'\x1b[1;1R')
                    output = (output + chunk)[-200000:]
                clean = re.sub(r'\x1b\[[0-?]*[ -/]*[@-~]', '', output)
                if re.search(r'(session\s+(?:created|started)|(?:created|started)\s+(?:a\s+)?(?:new\s+)?session|assistant\s*:|thinking\.\.\.|tool calls?\s*:)', clean, re.I):
                    raise KimiSessionError('Unexpected model session activity')
                windows = parse_kimi_screen(output, now())
                if len(windows) >= 2:
                    return windows
                if not workspace_confirmed and 'Trust this folder?' in clean and "Don't trust" in clean:
                    # This operator-created workspace is empty: no project MCP config.
                    os.write(master, b'\r')
                    workspace_confirmed = True
                if not sent and re.search(r'(Welcome to Kimi Code|│\s*>)', clean):
                    os.write(master, b'/usage')
                    sent, sent_at = True, time.monotonic()
                elif sent and enters == 0 and time.monotonic() - sent_at >= 2:
                    os.write(master, b'\r')
                    enters, sent_at = 1, time.monotonic()
                elif enters == 1 and time.monotonic() - sent_at >= 3 and re.search(r'→\s*usage\s+Show session tokens', clean):
                    os.write(master, b'\r')
                    enters = 2
            raise TimeoutError()
    finally:
        if process is not None and process.poll() is None:
            for _ in range(2):
                os.killpg(process.pid, signal.SIGINT)
                try:
                    process.wait(timeout=1)
                    break
                except subprocess.TimeoutExpired:
                    pass
            if process.poll() is None:
                os.killpg(process.pid, signal.SIGKILL)
                process.wait(timeout=2)
        os.close(master)
        if slave is not None:
            os.close(slave)


def probe(provider, cfg, result):
    if provider == 'moonshot':
        token = os.environ.get(cfg.get('key_env', ''), '')
        if not token:
            result['status'] = 'not_configured'
            return
        domain = cfg.get('domain', 'cn')
        if domain not in ('cn', 'ai'):
            raise ValueError()
        data = request(f'https://api.moonshot.{domain}/v1/users/me/balance', {'Authorization': 'Bearer ' + token}, cfg)
        amount = number(data.get('data', {}).get('available_balance'))
        if amount is None:
            raise ValueError()
        result['balance'] = {'amount': amount, 'currency': 'CNY' if domain == 'cn' else 'USD'}
        return
    if provider == 'kimi':
        try:
            result['windows'] = kimi_cli(cfg)
            result['source'] = 'cli'
            return
        except KimiSessionError:
            raise
        except Exception:
            pass
    # Re-read after CLI startup, which may have renewed its own credentials.
    credentials = json.loads(credential_path(provider).read_text(encoding='utf-8'))
    if provider == 'claude':
        auth = credentials['claudeAiOauth']
        token = auth['accessToken']
        result['plan'] = safe_plan(auth.get('subscriptionType'))
        headers = {'anthropic-beta': 'oauth-2025-04-20'}
        url = 'https://api.anthropic.com/api/oauth/usage'
    elif provider == 'codex':
        auth = credentials['tokens']
        token = auth['access_token']
        headers = {'ChatGPT-Account-Id': auth['account_id'], 'originator': 'codex_cli_rs'}
        url = 'https://chatgpt.com/backend-api/wham/usage'
    elif provider == 'grok':
        auth = next(iter(credentials.values()))
        token = auth['key']
        headers = {'x-xai-token-auth': 'xai-grok-cli'}
        url = 'https://cli-chat-proxy.grok.com/v1/billing'
    elif provider == 'cursor':
        auth = credentials
        token = auth['accessToken']
        sub = jwt(token)['sub'].split('|')[-1]
        headers = {'Cookie': 'WorkosCursorSessionToken=' + sub + '%3A%3A' + token,
                   'Origin': 'https://cursor.com', 'Referer': 'https://cursor.com/dashboard', 'Content-Type': 'application/json'}
        url = 'https://cursor.com/api/usage-summary'
    else:
        auth = credentials
        token = auth['access_token']
        headers = {}
        url = 'https://api.kimi.com/coding/v1/usages'
    claims = jwt(token)
    expiry = claims.get('exp')
    if provider == 'kimi':
        expiry = expiry or auth.get('expires_at')
        if expiry is None:
            result.update(status='expired', error='Credential expiry unavailable')
            return
    if expiry is not None:
        expired = expiry_time(expiry) <= now().timestamp()
        if expired:
            result.update(status='expired', error='Credential expired')
            return
    identity = auth.get('account_id') or claims.get('sub') or claims.get('email')
    if identity:
        result['account_fp'] = hashlib.sha256(str(identity).encode()).hexdigest()[:12]
    if provider != 'cursor':
        headers['Authorization'] = 'Bearer ' + token
    data = request(url, headers, cfg)
    extra = None
    if provider == 'grok':
        extra = request(url + '?format=credits', headers, cfg)
        settings = request('https://cli-chat-proxy.grok.com/v1/settings', headers, cfg)
        result['plan'] = safe_plan(settings.get('settings', settings).get('subscription_tier_display'))
    elif provider == 'cursor':
        extra = request('https://cursor.com/api/dashboard/get-sand-usage-status', headers, cfg, 'POST')
        result['plan'] = safe_plan(data.get('membershipType'))
    elif provider == 'codex':
        result['plan'] = safe_plan(data.get('plan_type'))
    result['windows'] = parse(provider, data, extra)
    if not result['windows']:
        raise ValueError()


def expiry_time(value):
    numeric = number(value)
    if numeric is not None:
        return numeric
    parsed = dt.datetime.fromisoformat(value.replace('Z', '+00:00'))
    if parsed.tzinfo is None:
        raise ValueError('Expiry requires timezone')
    return parsed.timestamp()


def safe_plan(value):
    # Vendor plan names only; arbitrary response strings cannot become identity leaks.
    allowed = ('free', 'pro', 'plus', 'team', 'max', 'ultra', 'enterprise', 'business', 'supergrok', 'supergrokheavy', 'basic', 'standard', 'premium', 'prolite', 'supergrok heavy', 'max 5x', 'max 20x')
    return value.lower() if isinstance(value, str) and value.lower() in allowed else None


REGISTRY = {p: probe if p in PROVIDERS[:6] else None for p in PROVIDERS}


def deduplicate_windows(windows):
    """Prefer named schema windows over numbered aliases of the same reading."""
    unique = {}
    for item in windows:
        identity = (item['period'], item['resets_at'], item['used_percent'])
        previous = unique.get(identity)
        numbered = lambda value: bool(re.search(r'(?:^|\.)\d+(?:\.|$)', value['key']))
        if previous is None or (numbered(previous) and not numbered(item)):
            unique[identity] = item
    return list(unique.values())


def collect(node, providers=None, config=None):
    config = load_config() if config is None else config
    # Without an explicit selection, report only consented providers so nodes
    # do not flood the board with consent_missing rows for every vendor.
    enabled = config.get('enabled_providers', [])
    selected = selection(providers) if providers else [p for p in PROVIDERS if p in enabled]
    fetched = iso(now())
    results = []
    for provider in selected:
        result = dict(provider=provider, kind='subscription' if provider in PROVIDERS[:5] else 'api',
                      status='ok', plan=None, account_fp=None, windows=[], balance=None,
                      fetched_at=fetched, error=None, source='api')
        if provider not in config.get('enabled_providers', []):
            result['status'] = 'consent_missing'
        elif REGISTRY[provider] is None:
            result.update(status='not_configured', error='Provider adapter not implemented')
        else:
            try:
                REGISTRY[provider](provider, config.get('providers', {}).get(provider, {}), result)
            except FileNotFoundError:
                result.update(status='not_configured', error='Credential or CLI unavailable')
            except urllib.error.HTTPError as exc:
                result.update(status='expired' if exc.code == 401 else 'error', error=f'Quota HTTP {exc.code}')
            except Exception:
                result.update(status='error', error='Quota query failed')
        result['windows'] = deduplicate_windows(result['windows'])
        results.append(result)
    return {'node': node, 'collected_at': fetched, 'providers': results}


def push(url, token, payload):
    req = urllib.request.Request(url.rstrip('/') + '/api/nodes/quota', method='POST',
                                 data=json.dumps(payload).encode(),
                                 headers={'Authorization': 'Bearer ' + token, 'Content-Type': 'application/json'})
    open_url(req, timeout=20, request_class=RequestClass.INWARD).close()
