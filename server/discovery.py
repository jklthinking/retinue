"""Privacy-preserving local runtime discovery for Retinue."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from node.data_dirs import scan as scan_data_dirs


RUNTIME_LABELS = {
    "claude-code": "Claude Code",
    "codex": "Codex",
    "kimi": "Kimi",
    "hermes": "Hermes",
    "openclaw": "OpenClaw",
}


def runtime_label(runtime: str) -> str:
    return RUNTIME_LABELS.get(runtime, runtime or "未命名运行时")


def scan_local_runtimes(home: Path | None = None) -> list[dict[str, Any]]:
    """Detect runtime directories without reading conversations or credentials.

    The detection itself lives in ``node.data_dirs.scan`` so the node probe
    and this server-side scan share a single implementation; this wrapper only
    adds the display label.
    """
    found = scan_data_dirs(home)
    for item in found:
        item["label"] = runtime_label(item["runtime"])
    return found
