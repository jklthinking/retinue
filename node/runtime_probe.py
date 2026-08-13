"""Discover terminal Agent CLIs and report only their availability to Retinue.

The probe never reads conversations, config files, environment variables, or
credentials.  It reports a stable runtime id and the executable basename only.

Detection must not depend on the caller's PATH: a scheduled probe (systemd,
Task Scheduler) has no login shell, so per-user install directories are absent
from its PATH even when a runtime is installed there.  Each command is
therefore looked up in a documented, ordered set of locations:

0.  an operator pin (``node.runtime_pins``): a per-node absolute executable
    path pinned in node-local configuration, checked first because it is the
    operator's explicit word for *this* machine;
1.  the process PATH (via ``shutil.which``);
2.  the conventional per-user binary directories:
    - POSIX:   ``~/.local/bin``, ``~/.cargo/bin``, ``~/bin``
    - Windows: ``~/.local/bin``, ``~/.cargo/bin``, ``~/AppData/Roaming/npm``
3.  the global package-manager binary directory:
    - POSIX:   ``/usr/local/bin``, ``/opt/homebrew/bin``
    - Windows: ``<SystemDrive>/ProgramData/chocolatey/bin``,
      ``<SystemDrive>/ProgramData/scoop/shims``

Explicit-directory lookups go through ``shutil.which(command, path=...)`` so
Windows executable-extension variants (PATHEXT: ``.exe``, ``.cmd``, ...) are
considered exactly as they are for PATH lookups.  Detection is existence-only
and cheap enough to run on a timer: no subprocess, no version probing, no
network.  A pinned binary is never executed either — the pin is consulted
with an existence check, nothing more.

``explain()`` answers the diagnostic question the bare payload cannot: *where
did you look, and what did you find there?*  It names real search locations
and is therefore local-only operator output — ``retinue-node runtimes
--explain`` prints it and pushes nothing.

The payload carries a second, independent signal under ``data_dirs``: the
runtime data directories detected by ``node.data_dirs.scan`` — the same
detection ``retinue scan`` prints locally.  A runtime can be in use with no
CLI on PATH (an editor extension drives it), and the server must see that
truth too.  Each entry is a stable runtime id, a tilde-relative ``path_hint``
such as ``~/.codex/sessions``, and the directory's last-modified time.

Privacy contract: the payload reports a stable runtime id, the executable
basename, availability, a coarse ``source`` label — ``"path"`` when the
command resolved on PATH, ``"well-known"`` when it resolved in one of the
conventional directories above, ``"pin"`` when an operator pin made it
available — and tilde-relative data-directory hints.  Every label says how,
never where: an absolute path is never reported or logged, and the probe
never reads a config file, an environment variable's
contents, a transcript, or a credential.  A pin's absolute path stays on the
node: the payload carries only the pinned executable's basename.
"""

from __future__ import annotations

import json
import os
import shutil
import urllib.request
from pathlib import Path, PurePath
from typing import Any

from . import data_dirs, runtime_pins
from .http_client import RequestClass, open_url


RUNTIME_COMMANDS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("codex", ("codex",)),
    ("claude-code", ("claude", "claude-code")),
    ("kimi", ("kimi", "kimi-cli")),
    ("hermes", ("hermes",)),
    ("openclaw", ("openclaw",)),
    ("opencode", ("opencode",)),
    ("gemini", ("gemini",)),
    ("github-copilot", ("copilot",)),
    ("cursor-agent", ("cursor-agent",)),
    ("kiro", ("kiro", "kiro-cli")),
)

SOURCE_PATH = "path"
SOURCE_WELL_KNOWN = "well-known"
SOURCE_PIN = "pin"


def _well_known_directories(home: PurePath, on_windows: bool) -> list[PurePath]:
    """Conventional install locations searched after PATH, in order."""
    if on_windows:
        directories: list[PurePath] = [
            home / ".local" / "bin",
            home / ".cargo" / "bin",
            home / "AppData" / "Roaming" / "npm",
        ]
        if home.anchor:
            program_data = type(home)(home.anchor) / "ProgramData"
            directories.extend(
                [
                    program_data / "chocolatey" / "bin",
                    program_data / "scoop" / "shims",
                ]
            )
        return directories
    return [
        home / ".local" / "bin",
        home / ".cargo" / "bin",
        home / "bin",
        PurePath("/usr/local/bin"),
        PurePath("/opt/homebrew/bin"),
    ]


def _search_directories() -> list[PurePath]:
    return _well_known_directories(Path.home(), os.name == "nt")


def _locate(command: str) -> str | None:
    """Return the source label for *command*, or None when it is not found."""
    if shutil.which(command):
        return SOURCE_PATH
    for directory in _search_directories():
        if shutil.which(command, path=os.fspath(directory)):
            return SOURCE_WELL_KNOWN
    return None


def _pinned_entry(
    runtime: str, pins: runtime_pins.RuntimePins
) -> dict[str, str | bool] | None:
    """The payload entry an operator pin provides, or None.

    Existence-only: the pinned binary is never executed.  The entry reports
    the executable's basename — the absolute path stays on the node.
    """
    pinned = pins.runtimes.get(runtime)
    if pinned is not None and os.path.isfile(pinned):
        return {
            "runtime": runtime,
            "command": os.path.basename(pinned),
            "available": True,
            "source": SOURCE_PIN,
        }
    return None


def collect(
    node_id: str, pins: runtime_pins.RuntimePins | None = None
) -> dict[str, Any]:
    """Return available agent CLIs and data-directory hints, never paths."""
    resolved = pins if pins is not None else runtime_pins.load()
    runtimes: list[dict[str, str | bool]] = []
    for runtime, commands in RUNTIME_COMMANDS:
        entry = _pinned_entry(runtime, resolved)
        if entry is not None:
            runtimes.append(entry)
            continue
        for command in commands:
            source = _locate(command)
            if source is not None:
                runtimes.append(
                    {
                        "runtime": runtime,
                        "command": command,
                        "available": True,
                        "source": source,
                    }
                )
                break
    return {"node_id": node_id, "runtimes": runtimes, "data_dirs": data_dirs.scan()}


def explain(pins: runtime_pins.RuntimePins | None = None) -> list[str]:
    """Local diagnostic lines: where each runtime was looked for, and what
    was found there.

    This output names real search locations, so it is for the operator's
    terminal only — it is never part of a payload, and ``--explain`` pushes
    nothing.  Detection stays existence-only.
    """
    resolved = pins if pins is not None else runtime_pins.load()
    known = {runtime for runtime, _ in RUNTIME_COMMANDS}
    searched = _search_directories()
    lines = [f"pin file: {runtime_pins.pins_file()}"]
    for runtime, commands in RUNTIME_COMMANDS:
        lines.append(f"{runtime}:")
        pinned = resolved.runtimes.get(runtime)
        if pinned is None:
            lines.append("  pin: not set")
            pin_hit = False
        elif os.path.isfile(pinned):
            lines.append(f"  pin: {pinned} (exists; used)")
            pin_hit = True
        else:
            lines.append(f"  pin: {pinned} (missing; ignored)")
            pin_hit = False
        path_hit: str | None = None
        for command in commands:
            found = shutil.which(command)
            lines.append(
                f"  PATH: {command}: " + (f"found at {found}" if found else "not found")
            )
            if found and path_hit is None:
                path_hit = command
        well_known_hit: tuple[str, PurePath] | None = None
        for command in commands:
            for directory in searched:
                if shutil.which(command, path=os.fspath(directory)):
                    well_known_hit = (command, directory)
                    break
            if well_known_hit is not None:
                break
        if well_known_hit is not None:
            lines.append(f"  well-known: {well_known_hit[0]} found in {well_known_hit[1]}")
        else:
            lines.append(
                "  well-known: not found in "
                + ", ".join(os.fspath(directory) for directory in searched)
            )
        if pin_hit:
            lines.append(f"  => available (source: {SOURCE_PIN})")
        elif path_hit is not None:
            lines.append(f"  => available (source: {SOURCE_PATH})")
        elif well_known_hit is not None:
            lines.append(f"  => available (source: {SOURCE_WELL_KNOWN})")
        else:
            lines.append("  => not found")
    for runtime in sorted(set(resolved.runtimes) - known):
        lines.append(f"pin for unknown runtime id ignored: {runtime}")
    lines.append("node interpreter (used by enroll):")
    if resolved.interpreter is None:
        lines.append("  pin: not set; enroll derives the interpreter at run time")
    elif os.path.isfile(resolved.interpreter):
        lines.append(f"  pin: {resolved.interpreter} (exists; used)")
    else:
        lines.append(f"  pin: {resolved.interpreter} (missing; enroll refuses)")
    return lines


def push(url: str, token: str, payload: dict[str, Any]) -> None:
    request = urllib.request.Request(
        url.rstrip("/") + "/api/nodes/runtimes",
        method="POST",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
    )
    open_url(request, timeout=15, request_class=RequestClass.INWARD).close()
