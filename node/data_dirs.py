"""Detect agent runtime data directories and report hints, never paths.

A runtime can be in use on a machine without any CLI on PATH: an editor
extension drives it, or the CLI was installed for another account.  The trace
such use leaves behind is a data directory in the user's home folder.  This
module detects those directories and reports them in exactly the form the
local scan (``retinue scan``) already prints: a tilde-relative hint such as
``~/.codex/sessions`` plus the directory's last-modified time.

Privacy contract: the hint names no user and no machine.  Detection is
existence-only — no file is opened, and no transcript, configuration value,
environment variable, or credential is read.  An absolute path is never
produced, reported, or logged.
"""

from __future__ import annotations

import datetime as dt
from pathlib import Path, PurePath
from typing import Any

# Candidate data directories per runtime, home-relative, first existing wins.
RUNTIME_DATA_DIRS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("codex", (".codex/sessions", ".codex")),
    ("claude-code", (".claude/projects", ".claude")),
    ("kimi", (".kimi/sessions", ".kimi")),
    ("hermes", (".hermes/dashboard-data", ".hermes")),
    ("openclaw", (".openclaw",)),
)


def _path_hint(candidate: PurePath, home: PurePath) -> str:
    """Return a platform-neutral, home-relative display hint."""
    return f"~/{candidate.relative_to(home).as_posix()}"


def scan(home: Path | None = None) -> list[dict[str, Any]]:
    """Return tilde-relative hints for the runtime data directories that exist."""
    root = home or Path.home()
    found: list[dict[str, Any]] = []
    for runtime, candidates in RUNTIME_DATA_DIRS:
        relative = next((item for item in candidates if (root / item).is_dir()), None)
        if relative is None:
            continue
        candidate = root / relative
        try:
            changed_at = dt.datetime.fromtimestamp(
                candidate.stat().st_mtime, tz=dt.timezone.utc
            ).isoformat()
        except OSError:
            changed_at = None
        found.append(
            {
                "runtime": runtime,
                "path_hint": _path_hint(candidate, root),
                "last_changed_at": changed_at,
            }
        )
    return found
