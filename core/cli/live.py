"""HTTP client for the Hub-backed Live Sessions CLI surface."""

from __future__ import annotations

import json
import os
from pathlib import Path
import urllib.error
import urllib.request
from typing import Any

from node.http_client import RequestClass, open_url

DEFAULT_URL = "http://127.0.0.1:9219"


class LiveCliError(RuntimeError):
    pass


def resolve_url(value: str | None) -> str:
    return value or os.environ.get("RETINUE_SERVER_URL") or DEFAULT_URL


def read_token(path: str | None) -> str:
    token_path = path or os.environ.get("RETINUE_TOKEN_FILE")
    if not token_path:
        raise LiveCliError(
            "live command requires --token-file or RETINUE_TOKEN_FILE"
        )
    try:
        token = Path(token_path).expanduser().read_text(encoding="utf-8").strip()
    except OSError as exc:
        raise LiveCliError("cannot read live-session token file") from exc
    if not token:
        raise LiveCliError("live-session token file is empty")
    return token


def call(
    method: str,
    path: str,
    *,
    url: str,
    token: str,
    body: dict[str, Any] | None = None,
) -> Any:
    request = urllib.request.Request(
        url.rstrip("/") + path,
        method=method,
        data=json.dumps(body).encode("utf-8") if body is not None else None,
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        },
    )
    try:
        response = open_url(request, timeout=15, request_class=RequestClass.INWARD)
        try:
            return json.load(response)
        finally:
            response.close()
    except urllib.error.HTTPError as exc:
        try:
            detail = json.loads(exc.read().decode("utf-8")).get("detail", str(exc))
        except Exception:
            detail = str(exc)
        raise LiveCliError(f"Hub rejected live command ({exc.code}): {detail}") from exc
    except urllib.error.URLError as exc:
        raise LiveCliError("cannot reach Retinue Hub") from exc


def list_live_sessions(*, url: str, token: str) -> list[dict[str, Any]]:
    return list(call("GET", "/api/live-sessions", url=url, token=token))


def create_control(
    live_session_id: str,
    verb: str,
    *,
    url: str,
    token: str,
    idempotency_key: str,
    message: str | None = None,
    lines: int | None = None,
) -> dict[str, Any]:
    body: dict[str, Any] = {
        "verb": verb,
        "idempotency_key": idempotency_key,
    }
    if message is not None:
        body["message"] = message
    if lines is not None:
        body["lines"] = lines
    return dict(
        call(
            "POST",
            f"/api/live-sessions/{live_session_id}/control",
            url=url,
            token=token,
            body=body,
        )
    )


def get_control(envelope_id: str, *, url: str, token: str) -> dict[str, Any]:
    return dict(
        call(
            "GET",
            f"/api/live-sessions/control/{envelope_id}",
            url=url,
            token=token,
        )
    )
