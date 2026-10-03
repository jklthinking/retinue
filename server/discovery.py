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


RUNTIME_ALIASES = {"openai-codex": "codex", "kimi-cli": "kimi"}
MODEL_ALIASES = {"opus", "sonnet", "haiku", "auto", "default"}
UNKNOWN_MODELS = {"", "unknown", "configured-at-runtime", "待确认", "未登记", "n/a"}


def canonical_runtime(runtime: str) -> str:
    """Normalize known inventory aliases without changing stored identities."""
    value = runtime.strip()
    return RUNTIME_ALIASES.get(value, value)


def is_sync_actor(actor: Any) -> bool:
    """Indexing services are transport agents, not model workers."""
    return actor.kind == "agent" and (
        actor.model.strip().lower().startswith("session-index-v")
        or actor.id.strip().lower().endswith("-session-sync")
    )


def model_identity_state(model: str) -> str:
    """Assess registry completeness, never provider verification."""
    value = model.strip().lower()
    if value in UNKNOWN_MODELS:
        return "unknown"
    return "alias" if value in MODEL_ALIASES else "registered"


def runtime_label(runtime: str) -> str:
    return RUNTIME_LABELS.get(canonical_runtime(runtime), runtime or "未命名运行时")


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
