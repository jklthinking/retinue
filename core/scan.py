"""Scan this machine for AI agents and report what is present.

Two existing detectors are reused rather than a third being written:

- ``node.runtime_probe.collect`` finds an agent's executable (its CLI);
- ``server.discovery.scan_local_runtimes`` finds a runtime's data directory.

They answer different questions and both matter. A machine can have a CLI but
no history (a fresh install), or history but no CLI (uninstalled, or another
user's runtime); the merged report keeps the two signals side by side so a
person can tell those situations apart.

Privacy contract, unchanged from the detectors themselves: the report carries
a runtime id, a display label, an executable basename, availability, and how
each signal was found — never an absolute path, never a transcript, never a
config file's contents. The scan executes no discovered binary, reads no file
contents, and makes no network call.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from node.runtime_probe import collect as collect_executables
from server.discovery import runtime_label, scan_local_runtimes

_SOURCE_TEXT = {
    "path": "on PATH",
    "well-known": "in a well-known install directory",
}


def scan_machine(home: Path | None = None) -> dict[str, Any]:
    """Merge executable and data-directory detections, keyed by runtime id."""
    agents: list[dict[str, Any]] = []
    by_runtime: dict[str, dict[str, Any]] = {}

    def slot(runtime: str) -> dict[str, Any]:
        if runtime not in by_runtime:
            entry = {
                "runtime": runtime,
                "label": runtime_label(runtime),
                "cli": None,
                "data": None,
            }
            by_runtime[runtime] = entry
            agents.append(entry)
        return by_runtime[runtime]

    # The node id is required by the probe's payload shape but is only used
    # when pushing to a server; it is never part of this local report.
    for hit in collect_executables("local")["runtimes"]:
        slot(hit["runtime"])["cli"] = {
            "command": hit["command"],
            "available": hit["available"],
            "source": hit["source"],
        }
    for hit in scan_local_runtimes(home):
        slot(hit["runtime"])["data"] = {
            "path_hint": hit["path_hint"],
            "last_changed_at": hit["last_changed_at"],
        }
    return {
        "agents": agents,
        "summary": {
            "agents_found": len(agents),
            "with_cli": sum(1 for agent in agents if agent["cli"] is not None),
            "with_data": sum(1 for agent in agents if agent["data"] is not None),
        },
    }


def render_text(report: dict[str, Any]) -> str:
    """Render the report for a terminal; a human reads this first."""
    lines = [
        "Agents on this machine (offline scan: no server, no account, no network)",
        "",
    ]
    if not report["agents"]:
        lines.append(
            "No agents found: no known agent CLI on PATH or in the well-known"
            " install directories, and no known runtime data directory in your"
            " home folder."
        )
        return "\n".join(lines)
    for agent in report["agents"]:
        lines.append(f"{agent['label']} ({agent['runtime']})")
        cli = agent["cli"]
        if cli is None:
            lines.append("  CLI:  not found")
        else:
            source = _SOURCE_TEXT.get(cli["source"], cli["source"])
            lines.append(f"  CLI:  {cli['command']} — available ({source})")
        data = agent["data"]
        if data is None:
            lines.append("  data: no local history found")
        else:
            changed = data["last_changed_at"] or "unknown"
            lines.append(f"  data: {data['path_hint']} — last changed {changed}")
        lines.append("")
    summary = report["summary"]
    lines.append(
        f"{summary['agents_found']} agent(s) found: "
        f"{summary['with_cli']} with a runnable CLI, "
        f"{summary['with_data']} with local history."
    )
    return "\n".join(lines)


def render_json(report: dict[str, Any]) -> str:
    """Render the same report for scripts; identical fields, JSON shape."""
    return json.dumps(report, ensure_ascii=False, indent=2)
