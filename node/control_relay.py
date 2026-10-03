"""Pull and execute generation-fenced live-session controls on one node.

The relay supports only ``tell``, ``peek``, and a single soft ``interrupt``.
It never evaluates a shell string. Tell uses a tmux buffer plus a separate
Enter key and is available only to a pane explicitly tagged ``codex-prompt``.
A leased tell is never automatically returned to the queue, so an uncertain
delivery cannot duplicate terminal input.
"""

from __future__ import annotations

import json
import re
import subprocess
import urllib.request
from typing import Any, Callable

from . import session_probe
from .http_client import RequestClass, open_url


class ControlRelayError(RuntimeError):
    pass


_SECRET_PATTERNS = (
    re.compile(r"\b(?:sk|rtn|rts|rtd)_[A-Za-z0-9_-]{16,}\b"),
    re.compile(r"\bghp_[A-Za-z0-9]{20,}\b"),
    re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~-]{12,}"),
)
_PRIVATE_PATH = re.compile(
    r"(?:(?:[A-Za-z]:)?[\\/](?:Users|home|root)[\\/])[^\s]+"
)


def _post(url: str, token: str, route: str, payload: dict[str, Any]) -> dict[str, Any]:
    request = urllib.request.Request(
        url.rstrip("/") + route,
        method="POST",
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        },
    )
    response = open_url(request, timeout=15, request_class=RequestClass.INWARD)
    try:
        return json.load(response)
    finally:
        response.close()


def pull(url: str, token: str, node_id: str, *, limit: int = 8) -> list[dict[str, Any]]:
    response = _post(
        url,
        token,
        "/api/live-sessions/control/pull",
        {"node_id": node_id, "limit": limit},
    )
    return list(response.get("controls", []))


def ack(url: str, token: str, payload: dict[str, Any]) -> dict[str, Any]:
    return _post(url, token, "/api/live-sessions/control/ack", payload)


def _run(
    argv: list[str],
    *,
    runner: Callable[..., subprocess.CompletedProcess[str]],
    input_text: str | None = None,
) -> subprocess.CompletedProcess[str]:
    try:
        result = runner(
            argv,
            input=input_text,
            check=False,
            capture_output=True,
            text=True,
            timeout=5,
        )
    except FileNotFoundError as exc:
        raise ControlRelayError("tmux is not available") from exc
    except subprocess.TimeoutExpired as exc:
        raise ControlRelayError("tmux control timed out") from exc
    if result.returncode != 0:
        raise ControlRelayError(
            f"tmux control failed with exit status {result.returncode}"
        )
    return result


def _target(
    envelope: dict[str, Any],
    node_id: str,
    *,
    socket_path: str | None,
    runner: Callable[..., subprocess.CompletedProcess[str]],
) -> dict[str, Any]:
    if envelope.get("node_id") != node_id or envelope.get("backend") != "tmux":
        raise ControlRelayError("control envelope targets another node or backend")
    inventory = session_probe.collect(
        node_id, socket_path=socket_path, runner=runner
    )
    matches = [
        pane
        for pane in inventory["panes"]
        if pane["endpoint_id"] == envelope.get("endpoint_id")
    ]
    if len(matches) != 1:
        raise ControlRelayError("target endpoint is missing or ambiguous")
    pane = matches[0]
    if pane["generation"] != envelope.get("generation"):
        raise ControlRelayError("target endpoint generation changed")
    if pane["live_session_id"] != envelope.get("live_session_id"):
        raise ControlRelayError("target live-session identity changed")
    if pane["actor_id"] != envelope.get("actor_id"):
        raise ControlRelayError("target actor identity changed")
    if pane["task_id"] != envelope.get("task_id"):
        raise ControlRelayError("target task identity changed")
    if not pane["control_eligible"]:
        raise ControlRelayError("target occupant is no longer verified")
    tmux_ref = envelope.get("tmux") or {}
    if pane["tmux"]["pane_id"] != tmux_ref.get("pane_id"):
        raise ControlRelayError("target pane identity changed")
    return pane


def _redact(text: str) -> str:
    redacted = text
    for pattern in _SECRET_PATTERNS:
        redacted = pattern.sub("[REDACTED]", redacted)
    redacted = _PRIVATE_PATH.sub("[PRIVATE_PATH]", redacted)
    return redacted[-8000:]


def execute(
    envelope: dict[str, Any],
    node_id: str,
    *,
    socket_path: str | None = None,
    runner: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
) -> dict[str, Any]:
    """Execute one already-leased envelope and return its ACK body."""
    envelope_id = envelope.get("id", "")
    generation = envelope.get("generation", "")
    base = {
        "node_id": node_id,
        "envelope_id": envelope_id,
        "generation": generation,
    }
    try:
        pane = _target(
            envelope, node_id, socket_path=socket_path, runner=runner
        )
        pane_id = pane["tmux"]["pane_id"]
        verb = envelope.get("verb")
        payload = envelope.get("payload") or {}
        if verb == "peek":
            lines = payload.get("lines", 20)
            if not isinstance(lines, int) or not 1 <= lines <= 100:
                raise ControlRelayError("peek line limit is invalid")
            result = _run(
                session_probe._tmux_argv(
                    socket_path,
                    "capture-pane",
                    "-p",
                    "-t",
                    pane_id,
                    "-S",
                    f"-{lines}",
                ),
                runner=runner,
            )
            return {
                **base,
                "outcome": "delivered",
                "result": _redact(result.stdout),
                "detail": "bounded peek captured and redacted on node",
            }
        if verb == "interrupt":
            _run(
                session_probe._tmux_argv(
                    socket_path, "send-keys", "-t", pane_id, "C-c"
                ),
                runner=runner,
            )
            return {
                **base,
                "outcome": "delivered",
                "result": "",
                "detail": "soft interrupt delivered once",
            }
        if verb != "tell":
            raise ControlRelayError("unsupported control verb")
        if (
            envelope.get("input_mode") != "codex-prompt"
            or pane.get("input_mode") != "codex-prompt"
        ):
            raise ControlRelayError("target has no current input-mode contract")
        message = payload.get("message")
        if (
            not isinstance(message, str)
            or not message
            or len(message) > 4000
            or any(ord(ch) < 32 and ch not in "\n\r\t" for ch in message)
            or chr(127) in message
        ):
            raise ControlRelayError("tell message is invalid")
        buffer_name = "retinue-" + envelope_id
        _run(
            session_probe._tmux_argv(
                socket_path, "load-buffer", "-b", buffer_name, "-"
            ),
            runner=runner,
            input_text=message,
        )
        _run(
            session_probe._tmux_argv(
                socket_path,
                "paste-buffer",
                "-b",
                buffer_name,
                "-t",
                pane_id,
                "-d",
            ),
            runner=runner,
        )
        _run(
            session_probe._tmux_argv(socket_path, "send-keys", "-t", pane_id, "Enter"),
            runner=runner,
        )
        return {
            **base,
            "outcome": "delivered",
            "result": "",
            "detail": "tmux paste-buffer delivered once",
        }
    except (ControlRelayError, ValueError) as exc:
        return {
            **base,
            "outcome": "failed",
            "result": "",
            "detail": str(exc)[:240],
        }


def relay_once(
    url: str,
    token: str,
    node_id: str,
    *,
    socket_path: str | None = None,
    limit: int = 8,
    runner: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
) -> list[dict[str, Any]]:
    acknowledgements: list[dict[str, Any]] = []
    for envelope in pull(url, token, node_id, limit=limit):
        result = execute(
            envelope, node_id, socket_path=socket_path, runner=runner
        )
        acknowledgements.append(ack(url, token, result))
    return acknowledgements
