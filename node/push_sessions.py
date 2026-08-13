"""Push privacy-scoped runtime session snapshots into Retinue Server."""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import Any

from adapters.exporters.sessions import collect_sessions

from .http_client import RequestClass, open_url


def push(url: str, token: str, rows: list[dict[str, Any]]) -> dict[str, int]:
    result = {"created": 0, "updated": 0, "unchanged": 0, "stale": 0}
    for row in rows:
        request = urllib.request.Request(
            url.rstrip("/") + "/api/sessions/sync",
            method="POST",
            data=json.dumps(row, ensure_ascii=False).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
            },
        )
        body = None
        for attempt in range(3):
            try:
                with open_url(request, timeout=20, request_class=RequestClass.INWARD) as response:
                    body = json.load(response)
                break
            except urllib.error.HTTPError as exc:
                detail = exc.read().decode("utf-8", "replace")[:300]
                try:
                    error_detail = json.loads(detail).get("detail")
                except (json.JSONDecodeError, AttributeError):
                    error_detail = None
                if exc.code == 409 and error_detail == "stale session cursor":
                    # The server already holds a longer immutable history.
                    # Keep it authoritative and continue the rest of the batch.
                    result["stale"] += 1
                    break
                raise RuntimeError(
                    f"session sync failed for {row['external_id']}: {exc.code} {detail}"
                ) from exc
            except (urllib.error.URLError, TimeoutError) as exc:
                if attempt == 2:
                    raise RuntimeError(
                        f"session sync transport failed for {row['external_id']}: {exc}"
                    ) from exc
        if body is None:
            continue
        status = body.get("sync_status")
        if status in result:
            result[status] += 1
    return result


def push_sessions(
    *,
    runtime: str,
    source: str,
    actor_id: str,
    url: str,
    token: str,
    privacy: str = "metadata",
    limit: int = 50,
    max_messages: int = 40,
) -> dict[str, int]:
    rows = collect_sessions(
        source,
        runtime=runtime,
        agent_id=actor_id,
        privacy=privacy,
        limit=limit,
        max_messages=max_messages,
    )
    return push(url, token, rows)
