"""Read-only discovery of Agent runtimes already visible through tmux.

This is deliberately not a control adapter.  It sends no text or key, reads no
scrollback, opens no database, and performs no network request.  The output is
safe to become a future session-probe payload: executable and cwd basenames are
reported, while the tmux socket path, command arguments, environment, terminal
content, transcript, and credentials stay on the node.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import urllib.request
from pathlib import PurePosixPath
from typing import Any, Callable

from .http_client import RequestClass, open_url

FIELD_SEPARATOR = "\x1f"
TMUX_FORMAT_FIELDS = (
    "#{session_id}",
    "#{session_name}",
    "#{window_id}",
    "#{window_index}",
    "#{window_name}",
    "#{pane_id}",
    "#{pane_index}",
    "#{pane_pid}",
    "#{pane_created}",
    "#{pane_current_command}",
    "#{pane_current_path}",
    "#{pane_dead}",
    "#{@retinue_session}",
    "#{@retinue_actor}",
    "#{@retinue_runtime}",
    "#{@retinue_task}",
    "#{@retinue_input_mode}",
)
TMUX_FORMAT = FIELD_SEPARATOR.join(TMUX_FORMAT_FIELDS)

RUNTIME_COMMANDS: dict[str, frozenset[str]] = {
    "codex": frozenset(("codex",)),
    "claude-code": frozenset(("claude", "claude-code")),
    "opencode": frozenset(("opencode",)),
    "kimi": frozenset(("kimi", "kimi-cli")),
    "gemini": frozenset(("gemini",)),
}
SLUG = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
SESSION_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,63}$")
TASK_ID = re.compile(r"^task-[0-9]{8}-[0-9]{3,}$")
TMUX_ID = re.compile(r"^[\$@%][0-9]+$")
INPUT_MODES = frozenset(("codex-prompt",))


class SessionProbeError(RuntimeError):
    """The local tmux probe could not produce a trustworthy inventory."""


def _digest(*parts: str, length: int = 24) -> str:
    material = "\x00".join(parts).encode("utf-8", errors="replace")
    return hashlib.sha256(material).hexdigest()[:length]


def _safe_text(value: str, limit: int) -> str:
    return "".join(ch for ch in value if ch >= " " and ch != "\x7f")[:limit]


def _safe_label(value: str, limit: int) -> str:
    """Drop path separators from display-only tmux labels."""
    return _safe_text(value, limit).replace("/", "·").replace("\\", "·")


def _safe_id(value: str, pattern: re.Pattern[str]) -> str:
    return value if pattern.fullmatch(value) else ""


def _basename(value: str) -> str:
    normalized = value.rstrip("/")
    if not normalized:
        return ""
    return _safe_text(PurePosixPath(normalized).name, 128)


def _runtime_for(command: str, explicit_runtime: str) -> tuple[str, str, str, int]:
    normalized = os.path.basename(command).lower()
    observed = ""
    for runtime, commands in RUNTIME_COMMANDS.items():
        if normalized in commands:
            observed = runtime
            break
    if SLUG.fullmatch(explicit_runtime):
        return explicit_runtime, observed, "explicit", 100
    if observed:
        return observed, observed, "process", 80
    return "", "", "heuristic", 0


def _truth(value: str) -> bool:
    return value == "1"


def _display_location(
    session_name: str, window_index: str, pane_index: str, pane_id: str
) -> str:
    session = _safe_label(session_name, 80) or "tmux"
    window = window_index if window_index.isdigit() else "?"
    pane = pane_index if pane_index.isdigit() else pane_id
    return f"{session}:{window}.{pane}"[:128]


def _parse_row(node_id: str, server_id: str, line: str) -> dict[str, Any] | None:
    values = line.split(FIELD_SEPARATOR)
    if len(values) != len(TMUX_FORMAT_FIELDS):
        return None
    (
        session_id,
        session_name,
        window_id,
        window_index,
        window_name,
        pane_id,
        pane_index,
        pane_pid,
        pane_created,
        command,
        cwd,
        pane_dead,
        explicit_session,
        explicit_actor,
        explicit_runtime,
        explicit_task,
        explicit_input_mode,
    ) = values

    session_id = _safe_id(session_id, TMUX_ID)
    window_id = _safe_id(window_id, TMUX_ID)
    pane_id = _safe_id(pane_id, TMUX_ID)
    if not session_id or not window_id or not pane_id or not pane_pid.isdigit():
        return None

    runtime, observed_runtime, binding_source, binding_confidence = _runtime_for(
        _basename(command), explicit_runtime
    )
    live_session_id = _safe_id(explicit_session, SESSION_ID)
    actor_id = _safe_id(explicit_actor, SLUG)
    task_id = _safe_id(explicit_task, TASK_ID)
    input_mode = explicit_input_mode if explicit_input_mode in INPUT_MODES else ""
    explicit_binding = bool(live_session_id and actor_id and runtime)
    if not explicit_binding and binding_source == "explicit":
        # Runtime-only metadata cannot authorize a binding.
        binding_source = "process" if observed_runtime else "heuristic"
        binding_confidence = 80 if observed_runtime else 0

    dead = _truth(pane_dead)
    occupant_verified = bool(runtime and observed_runtime == runtime)
    state = "disconnected" if dead else "unknown"
    state_source = "tmux" if dead else ("process" if observed_runtime else "heuristic")
    state_confidence = 100 if dead else (60 if observed_runtime else 0)
    endpoint_id = "tmux-" + _digest(node_id, server_id, pane_id)
    generation = _digest(server_id, pane_id, pane_pid, pane_created, length=32)

    return {
        "endpoint_id": endpoint_id,
        "generation": generation,
        "backend": "tmux",
        "runtime": runtime,
        "actor_id": actor_id or None,
        "live_session_id": live_session_id or None,
        "task_id": task_id or None,
        "input_mode": input_mode,
        "explicit_binding": explicit_binding,
        "binding_source": "explicit" if explicit_binding else binding_source,
        "binding_confidence": 100 if explicit_binding else binding_confidence,
        "occupant_verified": occupant_verified,
        "state": state,
        "state_source": state_source,
        "state_confidence": state_confidence,
        "command": _basename(command),
        "cwd_hint": _basename(cwd),
        "display_location": _display_location(
            session_name, window_index, pane_index, pane_id
        ),
        "tmux": {
            "session_id": session_id,
            "window_id": window_id,
            "pane_id": pane_id,
            "session_name": _safe_label(session_name, 80),
            "window_name": _safe_label(window_name, 80),
        },
        "control_eligible": explicit_binding and occupant_verified and not dead,
    }


def _is_no_server(stderr: str) -> bool:
    lowered = stderr.lower()
    return any(
        marker in lowered
        for marker in (
            "no server running", "failed to connect to server", "error connecting to"
        )
    )


def _tmux_argv(socket_path: str | None, *args: str) -> list[str]:
    argv = ["tmux"]
    if socket_path:
        argv.extend(("-S", socket_path))
    argv.extend(args)
    return argv


def collect(
    node_id: str,
    *,
    socket_path: str | None = None,
    runner: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
) -> dict[str, Any]:
    """Return a privacy-bounded inventory of panes on one tmux server.

    ``socket_path`` is an operator-selected local routing input.  Only its
    digest participates in opaque endpoint identity; the path never enters
    the returned payload or an exception message.
    """
    if not SLUG.fullmatch(node_id):
        raise ValueError("node_id must be a protocol slug")
    server_id = _digest("socket", socket_path or "default", length=16)
    argv = _tmux_argv(socket_path, "list-panes", "-a", "-F", TMUX_FORMAT)
    try:
        result = runner(
            argv,
            check=False,
            capture_output=True,
            text=True,
            timeout=5,
        )
    except FileNotFoundError:
        return {
            "node_id": node_id,
            "backend": "tmux",
            "server_id": server_id,
            "available": False,
            "status": "unavailable",
            "panes": [],
            "ignored_rows": 0,
        }
    except subprocess.TimeoutExpired as exc:
        raise SessionProbeError("tmux session probe timed out") from exc

    if result.returncode != 0:
        if _is_no_server(result.stderr or ""):
            return {
                "node_id": node_id,
                "backend": "tmux",
                "server_id": server_id,
                "available": True,
                "status": "no-server",
                "panes": [],
                "ignored_rows": 0,
            }
        raise SessionProbeError(
            f"tmux session probe failed with exit status {result.returncode}"
        )

    panes: list[dict[str, Any]] = []
    ignored_rows = 0
    for line in result.stdout.splitlines():
        if not line:
            continue
        parsed = _parse_row(node_id, server_id, line)
        if parsed is None:
            ignored_rows += 1
        else:
            panes.append(parsed)
    panes.sort(key=lambda item: (item["display_location"], item["endpoint_id"]))
    return {
        "node_id": node_id,
        "backend": "tmux",
        "server_id": server_id,
        "available": True,
        "status": "ok",
        "panes": panes,
        "ignored_rows": ignored_rows,
    }


def _run_tmux(
    argv: list[str],
    *,
    runner: Callable[..., subprocess.CompletedProcess[str]],
) -> None:
    try:
        result = runner(
            argv,
            check=False,
            capture_output=True,
            text=True,
            timeout=5,
        )
    except FileNotFoundError as exc:
        raise SessionProbeError("tmux is not available") from exc
    except subprocess.TimeoutExpired as exc:
        raise SessionProbeError("tmux metadata update timed out") from exc
    if result.returncode != 0:
        raise SessionProbeError(
            f"tmux metadata update failed with exit status {result.returncode}"
        )


def _find_pane(payload: dict[str, Any], pane_id: str) -> dict[str, Any]:
    matches = [item for item in payload["panes"] if item["tmux"]["pane_id"] == pane_id]
    if len(matches) != 1:
        raise SessionProbeError("target pane is missing or ambiguous")
    return matches[0]


def bind(
    node_id: str,
    *,
    pane_id: str,
    actor_id: str,
    live_session_id: str,
    runtime: str,
    task_id: str | None = None,
    input_mode: str | None = None,
    socket_path: str | None = None,
    runner: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
) -> dict[str, Any]:
    """Write Retinue user options to a verified pane without terminal input."""
    if not TMUX_ID.fullmatch(pane_id) or not pane_id.startswith("%"):
        raise ValueError("pane_id must be an exact tmux pane id")
    if not SLUG.fullmatch(actor_id):
        raise ValueError("actor_id must be a protocol slug")
    if not SESSION_ID.fullmatch(live_session_id):
        raise ValueError("live_session_id is invalid")
    if not SLUG.fullmatch(runtime):
        raise ValueError("runtime must be a protocol slug")
    if task_id is not None and not TASK_ID.fullmatch(task_id):
        raise ValueError("task_id is invalid")
    if input_mode is not None and input_mode not in INPUT_MODES:
        raise ValueError("input_mode is invalid")

    before = _find_pane(
        collect(node_id, socket_path=socket_path, runner=runner), pane_id
    )
    if before["state"] == "disconnected":
        raise SessionProbeError("target pane is disconnected")
    if before["runtime"] != runtime or not before["occupant_verified"]:
        raise SessionProbeError("target pane occupant does not match runtime")

    values = (
        ("@retinue_session", live_session_id),
        ("@retinue_actor", actor_id),
        ("@retinue_task", task_id or ""),
        ("@retinue_input_mode", input_mode or ""),
        ("@retinue_runtime", runtime),
    )
    try:
        for option, value in values:
            _run_tmux(
                _tmux_argv(
                    socket_path,
                    "set-option",
                    "-p",
                    "-t",
                    pane_id,
                    option,
                    value,
                ),
                runner=runner,
            )
    except SessionProbeError:
        for option, _value in reversed(values):
            try:
                _run_tmux(
                    _tmux_argv(
                        socket_path,
                        "set-option",
                        "-p",
                        "-u",
                        "-t",
                        pane_id,
                        option,
                    ),
                    runner=runner,
                )
            except SessionProbeError:
                pass
        raise

    after = _find_pane(
        collect(node_id, socket_path=socket_path, runner=runner), pane_id
    )
    if not after["control_eligible"]:
        raise SessionProbeError("tmux binding verification failed closed")
    return after


def unbind(
    node_id: str,
    *,
    pane_id: str,
    socket_path: str | None = None,
    runner: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
) -> dict[str, Any]:
    """Remove Retinue metadata from an exact pane, runtime option first."""
    if not TMUX_ID.fullmatch(pane_id) or not pane_id.startswith("%"):
        raise ValueError("pane_id must be an exact tmux pane id")
    _find_pane(collect(node_id, socket_path=socket_path, runner=runner), pane_id)
    for option in (
        "@retinue_runtime",
        "@retinue_input_mode",
        "@retinue_task",
        "@retinue_actor",
        "@retinue_session",
    ):
        _run_tmux(
            _tmux_argv(
                socket_path, "set-option", "-p", "-u", "-t", pane_id, option
            ),
            runner=runner,
        )
    return _find_pane(
        collect(node_id, socket_path=socket_path, runner=runner), pane_id
    )


def push(url: str, token: str, payload: dict[str, Any]) -> None:
    request = urllib.request.Request(
        url.rstrip("/") + "/api/live-sessions/probe",
        method="POST",
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        },
    )
    open_url(request, timeout=15, request_class=RequestClass.INWARD).close()
