"""One quota-only pull cycle. Remote fields never become commands or paths."""
from __future__ import annotations

from contextlib import contextmanager
import datetime as dt
import json
import os
import re
import signal
import threading
import time
import urllib.request

from . import quota_probe as quota
from .http_client import RequestClass, open_url


@contextmanager
def collection_lock():
    path = quota.config_file().with_suffix(".lock")
    path.parent.mkdir(parents=True, exist_ok=True)
    stream = path.open("a+b")
    acquired = False
    try:
        if os.name == "nt":
            import msvcrt
            if stream.tell() == 0:
                stream.write(b"0")
                stream.flush()
            stream.seek(0)
            try:
                msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
                acquired = True
            except OSError:
                pass
        else:
            import fcntl
            try:
                fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                acquired = True
            except BlockingIOError:
                pass
        yield acquired
    finally:
        if acquired:
            if os.name == "nt":
                import msvcrt
                stream.seek(0)
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                fcntl.flock(stream.fileno(), fcntl.LOCK_UN)
        stream.close()


def claim(url, token, node):
    request = urllib.request.Request(url.rstrip("/") + "/api/nodes/quota/refresh/claim",
        data=json.dumps({"node": node}).encode(), method="POST",
        headers={"Authorization": "Bearer " + token, "Content-Type": "application/json"})
    with open_url(request, timeout=15, request_class=RequestClass.INWARD) as response:
        if response.status == 204:
            return None
        # A compromised response cannot turn a poll into an execution adapter.
        raw = response.read(16 * 1024 + 1)
        if len(raw) > 16 * 1024:
            raise ValueError("oversized quota claim")
        body = json.loads(raw)
    if not isinstance(body, dict) or set(body) != {"id", "type", "providers", "deadline", "deadline_in"}:
        raise ValueError("invalid quota claim")
    if body["type"] != "quota_refresh" or not isinstance(body["id"], str) or not re.fullmatch(r"[a-f0-9]{32}", body["id"]):
        raise ValueError("invalid quota claim")
    providers = body["providers"]
    if providers is not None and (not isinstance(providers, list) or not providers or len(providers) > 10 or
            any(not isinstance(p, str) or p not in quota.PROVIDERS for p in providers) or len(set(providers)) != len(providers)):
        raise ValueError("invalid quota scope")
    deadline = dt.datetime.fromisoformat(body["deadline"].replace("Z", "+00:00"))
    if deadline.tzinfo is None or type(body["deadline_in"]) is not int or not 1 <= body["deadline_in"] <= 180:
        raise ValueError("invalid quota deadline")
    return body


class CollectionDeadline(BaseException):
    """BaseException deliberately escapes collectors' per-provider catches."""


def failed_provider(provider, fetched):
    return dict(provider=provider, kind="subscription" if provider in quota.PROVIDERS[:5] else "api",
                status="error", plan=None, account_fp=None, windows=[], balance=None,
                fetched_at=fetched, error="Quota collection timed out", source="api")


def _process_worker(node, selected, config, channel):
    # Fixed internal callable, used where SIGALRM is unavailable. No received
    # field is interpreted as argv, executable, URL, or a filesystem path.
    channel.put(quota.collect(node, selected, config))


def bounded_collect(node, providers, config, seconds=150):
    selected = list(providers) if providers is not None else quota.selection(config.get("enabled_providers", []))
    fetched = quota.iso(quota.now())
    payload = {"node": node, "collected_at": fetched, "providers": []}
    if not selected:
        return payload
    if not hasattr(signal, "SIGALRM") or threading.current_thread() is not threading.main_thread():
        import multiprocessing
        context = multiprocessing.get_context("spawn")
        channel = context.Queue(maxsize=1)
        process = context.Process(target=_process_worker, args=(node, selected, config, channel))
        process.start()
        try:
            import queue
            try:
                return channel.get(timeout=seconds)
            except queue.Empty:
                payload["providers"] = [failed_provider(p, fetched) for p in selected]
                return payload
        finally:
            if process.is_alive():
                process.terminate()
            process.join(timeout=3)
            if process.is_alive():
                process.kill()
                process.join(timeout=2)
            channel.close()
    old_handler = signal.getsignal(signal.SIGALRM)
    def timeout(signum, frame):
        raise CollectionDeadline()
    signal.signal(signal.SIGALRM, timeout)
    previous_timer = signal.setitimer(signal.ITIMER_REAL, seconds)
    try:
        for provider in selected:
            # Passing the requested subset preserves consent_missing evidence;
            # collect itself guards credentials/CLI behind local consent.
            payload["providers"].extend(quota.collect(node, [provider], config)["providers"])
    except CollectionDeadline:
        completed = {row["provider"] for row in payload["providers"]}
        payload["providers"].extend(failed_provider(p, fetched) for p in selected if p not in completed)
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, old_handler)
        if previous_timer[0] or previous_timer[1]:
            signal.setitimer(signal.ITIMER_REAL, *previous_timer)
    return payload


def poll(url, token, node):
    with collection_lock() as acquired:
        if not acquired:
            return "busy"
        started = time.monotonic()
        request = claim(url, token, node)
        if request is None:
            return "idle"
        config = quota.load_config()
        # Local collection budget leaves time for cleanup and the HTTP report.
        budget = min(150, max(1, request["deadline_in"] - (time.monotonic() - started) - 25))
        payload = bounded_collect(node, request["providers"], config, budget)
        payload["refresh_request_id"] = request["id"]
        quota.push(url, token, payload)
        return "reported"
