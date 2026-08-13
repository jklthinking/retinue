"""Claim-time skill briefing for the generic board worker.

The server is the source of truth. This module fetches the bound-skill
briefing, keeps a local cache, and retries transient failures. The generic
worker should call ``load_bound_skill_briefing`` after identity load and
``bound_skill_names`` in place of owner-list filtering.

Cache files live in a caller-chosen directory. This module never invents a
machine path and never writes tokens into the cache.
"""

from __future__ import annotations

import json
import re
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Callable

_SAFE_NAME = re.compile(r"[^\w\u4e00-\u9fff-]+", re.UNICODE)
_TRANSIENT_STATUS = {408, 425, 429, 500, 502, 503, 504}


class DispatchError(RuntimeError):
    """Briefing could not be loaded and no usable cache exists."""


def bound_skill_names(briefing: dict[str, Any]) -> set[str]:
    """Names the generic board worker should treat as this actor's bound skills."""
    names: set[str] = set()
    for row in briefing.get("skills") or []:
        name = row.get("name")
        if isinstance(name, str) and name.strip():
            names.add(name)
    return names


def briefing_prompt_block(briefing: dict[str, Any]) -> str:
    """Compact block the worker can append to a start-of-work prompt."""
    skills = briefing.get("skills") or []
    if not skills:
        return "Bound skills: none."
    lines = ["Bound skills for this run:"]
    for row in skills:
        name = row.get("name") or "unnamed"
        category = row.get("category") or "uncategorized"
        description = (row.get("description") or "").strip()
        risk = row.get("risk_notice")
        summary = description.splitlines()[0][:160] if description else "no description"
        lines.append(f"- {name} ({category}): {summary}")
        if risk:
            lines.append(f"  risk: {risk}")
    note = briefing.get("note")
    if isinstance(note, str) and note.strip():
        lines.append(note.strip())
    return "\n".join(lines)


def cache_path(cache_dir: Path, actor_id: str) -> Path:
    safe = _SAFE_NAME.sub("-", actor_id).strip("-") or "actor"
    return cache_dir / f"briefing-{safe}.json"


def read_cached_briefing(cache_dir: Path, actor_id: str) -> dict[str, Any] | None:
    path = cache_path(cache_dir, actor_id)
    if not path.is_file():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict):
        return None
    return payload


def write_cached_briefing(cache_dir: Path, actor_id: str, briefing: dict[str, Any]) -> Path:
    cache_dir.mkdir(parents=True, exist_ok=True)
    path = cache_path(cache_dir, actor_id)
    stored = {
        "actor_id": briefing.get("actor_id") or actor_id,
        "display_name": briefing.get("display_name") or actor_id,
        "skills": briefing.get("skills") or [],
        "count": briefing.get("count") or len(briefing.get("skills") or []),
        "note": briefing.get("note") or "",
    }
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(stored, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)
    return path


def fetch_skill_briefing(
    get_payload: Callable[[], dict[str, Any]],
    *,
    cache_dir: Path,
    actor_id: str,
    retries: int = 3,
    backoff_seconds: float = 0.05,
    sleep: Callable[[float], None] = time.sleep,
) -> dict[str, Any]:
    """Load a briefing with retry, then persist it. Serve cache if every try fails."""
    last_error: Exception | None = None
    attempts = max(1, retries)
    for attempt in range(attempts):
        try:
            payload = get_payload()
            if not isinstance(payload, dict):
                raise DispatchError("briefing payload is not an object")
            write_cached_briefing(cache_dir, actor_id, payload)
            return payload
        except Exception as exc:  # noqa: BLE001 — caller decides retry vs cache
            last_error = exc
            if attempt + 1 >= attempts or not _is_transient(exc):
                break
            sleep(backoff_seconds * (2**attempt))
    cached = read_cached_briefing(cache_dir, actor_id)
    if cached is not None:
        cached = dict(cached)
        cached["from_cache"] = True
        return cached
    raise DispatchError(f"skill briefing unavailable: {last_error}") from last_error


def _is_transient(exc: BaseException) -> bool:
    if isinstance(exc, urllib.error.HTTPError):
        return exc.code in _TRANSIENT_STATUS
    if isinstance(exc, (TimeoutError, ConnectionError, urllib.error.URLError)):
        return True
    return False


def load_bound_skill_briefing(
    base_url: str,
    token: str,
    actor_id: str,
    cache_dir: Path,
    *,
    retries: int = 3,
    timeout: float = 20,
    opener: Callable[..., Any] | None = None,
) -> dict[str, Any]:
    """HTTP helper the generic worker can call after claim or on poll."""

    def get_payload() -> dict[str, Any]:
        request = urllib.request.Request(
            base_url.rstrip("/") + "/api/me/skill-briefing",
            method="GET",
            headers={"Authorization": f"Bearer {token}"},
        )
        open_fn = opener or urllib.request.urlopen
        with open_fn(request, timeout=timeout) as response:
            raw = response.read().decode("utf-8")
            if not raw:
                raise DispatchError("empty briefing response")
            payload = json.loads(raw)
            if not isinstance(payload, dict):
                raise DispatchError("briefing payload is not an object")
            return payload

    return fetch_skill_briefing(
        get_payload, cache_dir=cache_dir, actor_id=actor_id, retries=retries
    )


def safe_skill_dirname(name: str) -> str:
    cleaned = _SAFE_NAME.sub("-", name).strip("-")
    return cleaned or "skill"


def materialize_briefing(briefing: dict[str, Any], dest_dir: Path) -> list[str]:
    """Write one SKILL card per bound skill into dest_dir. Returns written names."""
    dest_dir.mkdir(parents=True, exist_ok=True)
    written: list[str] = []
    for row in briefing.get("skills") or []:
        name = row.get("name")
        if not isinstance(name, str) or not name.strip():
            continue
        folder = dest_dir / safe_skill_dirname(name)
        folder.mkdir(parents=True, exist_ok=True)
        risk = row.get("risk_notice")
        body = [
            f"# {name}",
            "",
            f"category: {row.get('category') or 'uncategorized'}",
            f"source: {row.get('source') or 'local'}",
            f"source_kind: {row.get('source_kind') or 'local'}",
            "",
            (row.get("description") or "").strip() or "No description.",
            "",
        ]
        if risk:
            body.extend(["## Risk", "", str(risk), ""])
        (folder / "SKILL.md").write_text("\n".join(body), encoding="utf-8")
        written.append(name)
    return written
