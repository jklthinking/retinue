"""Fire-and-forget QC webhook when a board card reaches ``done`` (M0).

External QC bots are notified out-of-band; model workers never enter RETINUE.
Configuration defaults to off. Failures are logged only and must never affect
the completion transition that triggered the hook.
"""

from __future__ import annotations

import datetime as dt
import json
import logging
import os
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

import yaml

from .db import Task, utcnow
from .http_client import RequestClass, open_url

logger = logging.getLogger(__name__)

CONFIG_FILENAME = "qc_hook.yaml"
DEDUPE_FILENAME = "qc_hook_deliveries.json"
DEFAULT_URL_ENV = "RETINUE_QC_HOOK_URL"
DEFAULT_TIMEOUT_SECONDS = 3.0

HttpPost = Callable[[str, bytes, float], tuple[int, str]]

_bound_data_dir: Path | None = None
# Process-local fallback when no data_dir is available (tests / early calls).
_memory_sent: set[str] = set()


@dataclass
class QcHookConfig:
    enabled: bool = False
    url_env: str = DEFAULT_URL_ENV
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS


def bind_data_dir(data_dir: Path | str | None) -> None:
    """Remember the process data directory (mirrors reminders/notify)."""
    global _bound_data_dir
    _bound_data_dir = Path(data_dir) if data_dir else None


def bound_data_dir() -> Path | None:
    return _bound_data_dir


def reset_memory_dedupe() -> None:
    """Test helper: clear in-process dedupe keys."""
    _memory_sent.clear()


def load_qc_hook_config(data_dir: Path | str | None) -> QcHookConfig:
    """Load ``<data_dir>/qc_hook.yaml``. Missing file => inert defaults."""
    cfg = QcHookConfig()
    if not data_dir:
        return cfg
    path = Path(data_dir) / CONFIG_FILENAME
    if not path.is_file():
        return cfg
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError) as exc:
        logger.warning("qc_hook config unreadable: %s", exc.__class__.__name__)
        return cfg
    if not isinstance(raw, dict):
        return cfg
    cfg.enabled = bool(raw.get("enabled", False))
    url_env = raw.get("url_env", DEFAULT_URL_ENV)
    if isinstance(url_env, str) and url_env.strip():
        cfg.url_env = url_env.strip()
    try:
        cfg.timeout_seconds = float(raw.get("timeout_seconds", DEFAULT_TIMEOUT_SECONDS))
    except (TypeError, ValueError):
        cfg.timeout_seconds = DEFAULT_TIMEOUT_SECONDS
    if cfg.timeout_seconds <= 0:
        cfg.timeout_seconds = DEFAULT_TIMEOUT_SECONDS
    return cfg


def dedupe_key_for(task_id: str) -> str:
    return f"qc:{task_id}"


def _iso(value: dt.datetime) -> str:
    aware = value if value.tzinfo is not None else value.replace(tzinfo=dt.timezone.utc)
    return aware.astimezone(dt.timezone.utc).isoformat().replace("+00:00", "Z")


def _default_http_post(url: str, body: bytes, timeout: float) -> tuple[int, str]:
    request = urllib.request.Request(
        url,
        data=body,
        method="POST",
        headers={"Content-Type": "application/json; charset=utf-8"},
    )
    try:
        with open_url(
            request, timeout=timeout, request_class=RequestClass.OUTWARD
        ) as response:
            status = int(getattr(response, "status", 0) or 0)
            response.read()
            return status, ""
    except urllib.error.HTTPError as exc:
        return int(exc.code), f"http_{exc.code}"
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        return 0, exc.__class__.__name__


def _load_sent(data_dir: Path | None) -> set[str]:
    if data_dir is None:
        return set(_memory_sent)
    path = data_dir / DEDUPE_FILENAME
    if not path.is_file():
        return set()
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return set()
    if isinstance(raw, list):
        return {str(item) for item in raw}
    if isinstance(raw, dict) and isinstance(raw.get("keys"), list):
        return {str(item) for item in raw["keys"]}
    return set()


def _store_sent(data_dir: Path | None, keys: set[str]) -> None:
    if data_dir is None:
        _memory_sent.clear()
        _memory_sent.update(keys)
        return
    data_dir.mkdir(parents=True, exist_ok=True)
    path = data_dir / DEDUPE_FILENAME
    payload = {"keys": sorted(keys)}
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def _already_sent(data_dir: Path | None, key: str) -> bool:
    return key in _load_sent(data_dir)


def _mark_sent(data_dir: Path | None, key: str) -> None:
    keys = _load_sent(data_dir)
    keys.add(key)
    _store_sent(data_dir, keys)


def maybe_notify_task_done(
    task: Task,
    *,
    data_dir: Path | str | None = None,
    config: QcHookConfig | None = None,
    http_post: HttpPost | None = None,
    now: dt.datetime | None = None,
) -> str:
    """Notify an external QC bot that ``task`` just reached done.

    Returns a short action token for tests: ``disabled``, ``no_url``,
    ``duplicate``, ``sent``, or ``failed``. Never raises to callers that
    wrap this in their own guard; internal errors are logged and returned
    as ``failed``.
    """
    try:
        if data_dir is not None:
            resolved_dir: Path | None = Path(data_dir)
        else:
            resolved_dir = bound_data_dir()
        cfg = config if config is not None else load_qc_hook_config(resolved_dir)
        if not cfg.enabled:
            return "disabled"
        url = (os.environ.get(cfg.url_env) or "").strip()
        if not url:
            return "no_url"
        key = dedupe_key_for(task.id)
        if _already_sent(resolved_dir, key):
            return "duplicate"
        clock = now or utcnow()
        payload: dict[str, Any] = {
            "task_id": task.id,
            "title": task.title,
            "holder": task.holder,
            "done_at": _iso(clock),
        }
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        poster = http_post or _default_http_post
        status, err = poster(url, body, float(cfg.timeout_seconds))
        if status < 200 or status >= 300:
            logger.warning(
                "qc_hook POST failed for %s: status=%s err=%s",
                task.id,
                status,
                err or "non_2xx",
            )
            return "failed"
        _mark_sent(resolved_dir, key)
        return "sent"
    except Exception:
        logger.exception("qc_hook failed for task %s", getattr(task, "id", "?"))
        return "failed"
