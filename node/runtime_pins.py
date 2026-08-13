"""Per-node executable pins: node-local configuration that never travels.

A runtime id does not name the same executable on every machine.  One node's
default interpreter lacks venv support while a second interpreter on the same
machine works; another node's agent CLI lives outside every directory the
scheduled search consults.  A pin is the operator's per-node answer: "on
*this* machine, use *this* exact executable".  The same runtime id can be
pinned to different paths on different machines — the pin belongs to the
node, never to a shared profile.

A pin lives in exactly one place: a JSON file on the node itself, found
through ``RETINUE_RUNTIME_PINS_FILE`` when set, otherwise at

- POSIX:   ``~/.config/retinue/runtime-pins.json``
- Windows: ``~/AppData/Roaming/retinue/runtime-pins.json``

Format (every key optional; every value an absolute path)::

    {
      "interpreter": "/abs/path/to/python3.11",
      "runtimes": {
        "codex": "/abs/path/to/codex",
        "claude-code": "/abs/path/to/claude"
      }
    }

``interpreter`` is the Python enrolment renders into the node duties'
schedule; each ``runtimes`` entry is the executable the inventory treats as
that runtime.

Privacy contract: pins are absolute paths by nature, so they are confined to
the node.  The file sits outside every tracked directory; the inventory
payload reports only the pinned executable's *basename* and the source label
``"pin"``; the only other place a pin can surface is the operator's own
terminal (a local refusal message, a rendered unit that is written only to
the node's unit directories, or the explicitly requested ``--explain``
output).  A pinned path never enters a server payload, a server response, a
tracked file, or a log line.  Loading and checking pins is existence-only —
no pinned binary is ever executed.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path

ENV_PINS_FILE = "RETINUE_RUNTIME_PINS_FILE"


@dataclass(frozen=True)
class RuntimePins:
    """The operator's per-node executable overrides; empty means derive."""

    interpreter: str | None = None  # Python for the scheduled node duties
    runtimes: dict[str, str] = field(default_factory=dict)  # runtime id -> executable


def default_pins_file(home: Path, on_windows: bool) -> Path:
    """The conventional pin-file location for the given home and platform."""
    if on_windows:
        return home / "AppData" / "Roaming" / "retinue" / "runtime-pins.json"
    return home / ".config" / "retinue" / "runtime-pins.json"


def pins_file() -> Path:
    """Where pins are read from: the environment override wins."""
    override = os.environ.get(ENV_PINS_FILE)
    if override:
        return Path(override).expanduser()
    return default_pins_file(Path.home(), os.name == "nt")


def _require_absolute(value: object, what: str, location: Path) -> str:
    if not isinstance(value, str) or not value.strip():
        raise SystemExit(f"Runtime pin 文件 {location}: {what} 必须是非空字符串")
    if not os.path.isabs(value):
        raise SystemExit(f"Runtime pin 文件 {location}: {what} 必须是绝对路径: {value!r}")
    return value


def load(path: Path | None = None) -> RuntimePins:
    """Read the pin file, or empty pins when no file exists.

    A malformed file fails fast with a message naming the file: a silently
    ignored pin is exactly the "not found with no way to ask why" failure
    this mechanism exists to eliminate.
    """
    location = path or pins_file()
    try:
        text = location.read_text(encoding="utf-8")
    except FileNotFoundError:
        return RuntimePins()
    except OSError as exc:
        raise SystemExit(f"Runtime pin 文件不可读: {location} ({exc})") from None
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        raise SystemExit(f"Runtime pin 文件不是有效 JSON: {location}") from None
    if not isinstance(data, dict):
        raise SystemExit(f"Runtime pin 文件 {location}: 顶层必须是 JSON 对象")
    unknown = sorted(set(data) - {"interpreter", "runtimes"})
    if unknown:
        raise SystemExit(
            f"Runtime pin 文件 {location}: 未知键: {', '.join(unknown)}"
            "(可选: interpreter, runtimes)"
        )
    interpreter = data.get("interpreter")
    if interpreter is not None:
        interpreter = _require_absolute(interpreter, "interpreter", location)
    runtimes_raw = data.get("runtimes", {})
    if not isinstance(runtimes_raw, dict):
        raise SystemExit(f"Runtime pin 文件 {location}: runtimes 必须是 JSON 对象")
    runtimes = {
        str(runtime): _require_absolute(value, f"runtimes.{runtime}", location)
        for runtime, value in runtimes_raw.items()
    }
    return RuntimePins(interpreter=interpreter, runtimes=runtimes)
