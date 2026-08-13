"""Push de-identified runtime usage into the Retinue Server metrics API.

Reads local Claude Code / Codex transcripts with the existing read-only
exporters (token counts only — no session bodies, prompts, or keys leave
the machine they were recorded on) and upserts the last 7 daily buckets.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import Any

from .http_client import RequestClass, open_url

INPUT_FIELDS = ("input_tokens", "cache_creation_input_tokens", "cache_read_input_tokens")


def collect(runtime: str, source: str, actor_id: str) -> dict[str, Any]:
    if runtime == "claude-code":
        from adapters.exporters.claude_code import collect_metrics
    elif runtime == "codex":
        from adapters.exporters.codex import collect_metrics
    else:
        raise ValueError(f"unsupported runtime: {runtime!r}")
    return collect_metrics(source, agent_id=actor_id)


def daily_rows(snapshot: dict[str, Any], actor_id: str, runtime: str) -> list[dict[str, Any]]:
    rows = []
    for bucket in snapshot["last_7_days"]["daily"]:
        rows.append(
            {
                "actor_id": actor_id,
                "date": bucket["date"],
                "runtime": runtime,
                "input_tokens": sum(bucket.get(field, 0) for field in INPUT_FIELDS),
                "output_tokens": bucket.get("output_tokens", 0),
            }
        )
    return rows


def push(url: str, token: str, rows: list[dict[str, Any]]) -> int:
    pushed = 0
    for row in rows:
        request = urllib.request.Request(
            url.rstrip("/") + "/api/metrics/ingest",
            method="POST",
            data=json.dumps(row).encode("utf-8"),
            headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        )
        try:
            with open_url(request, timeout=15, request_class=RequestClass.INWARD):
                pushed += 1
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", "replace")[:200]
            raise RuntimeError(f"ingest failed for {row['date']}: {exc.code} {detail}") from exc
    return pushed


def push_usage(*, runtime: str, source: str, actor_id: str, url: str, token: str) -> int:
    snapshot = collect(runtime, source, actor_id)
    return push(url, token, daily_rows(snapshot, actor_id, runtime))
