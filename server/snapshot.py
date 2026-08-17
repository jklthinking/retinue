"""Static board snapshot for read-only fallback when the server is down.

Writes a deterministic JSON projection and a matching human-readable HTML page
under ``<data-dir>/snapshots/``. The snapshot is derived only from task rows
already in the database; it never mutates the database or task cards.
"""

from __future__ import annotations

import datetime as dt
import html
import json
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from core.protocol.task import STATES

from .db import Task

SNAPSHOT_DIRNAME = "snapshots"
JSON_FILENAME = "board.json"
HTML_FILENAME = "board.html"

# Projection fields only — keep the offline file small and stable.
_TASK_FIELDS = ("id", "title", "status", "holder", "progress", "priority")


def _iso(value: dt.datetime) -> str:
    if value.tzinfo is None:
        value = value.replace(tzinfo=dt.timezone.utc)
    else:
        value = value.astimezone(dt.timezone.utc)
    return value.isoformat().replace("+00:00", "Z")


def _project_task(task: Task) -> dict[str, Any]:
    return {
        "id": task.id,
        "title": task.title,
        "status": task.status,
        "holder": task.holder,
        "progress": int(task.progress or 0),
        "priority": task.priority,
    }


def _generated_at(tasks: list[Task], now: dt.datetime | None) -> str | None:
    """Prefer the latest task ``updated_at`` so a no-op re-run stays stable.

    When the board is empty there is no task clock to reuse. Callers may inject
    ``now`` (tests do); otherwise the field is omitted rather than embedding the
    wall clock of this run.
    """
    stamps = [task.updated_at for task in tasks if task.updated_at is not None]
    if stamps:
        return _iso(max(stamps))
    if now is not None:
        return _iso(now)
    return None


def build_board_payload(
    db: Session, *, now: dt.datetime | None = None
) -> dict[str, Any]:
    """Build the deterministic board projection from the open session."""
    tasks = list(
        db.execute(
            select(Task).where(Task.archived.is_(False)).order_by(Task.id)
        ).scalars()
    )
    counts = {status: 0 for status in STATES}
    projected: list[dict[str, Any]] = []
    for task in tasks:
        row = _project_task(task)
        projected.append(row)
        if task.status in counts:
            counts[task.status] += 1
        else:
            # Unknown status still appears in counts so the file reflects reality.
            counts[task.status] = counts.get(task.status, 0) + 1

    payload: dict[str, Any] = {
        "task_counts": counts,
        "tasks": projected,
    }
    generated = _generated_at(tasks, now)
    if generated is not None:
        payload["generated_at"] = generated
    return payload


def render_board_html(payload: dict[str, Any]) -> str:
    """Render a self-contained, script-free HTML page for the same payload."""
    counts = payload.get("task_counts") or {}
    tasks = payload.get("tasks") or []
    generated = payload.get("generated_at")

    count_items = "".join(
        f"<li><span class=\"status\">{html.escape(str(status))}</span>"
        f": {int(count)}</li>"
        for status, count in sorted(counts.items(), key=lambda item: item[0])
    )
    rows = []
    for task in tasks:
        cells = "".join(
            f"<td>{html.escape(str(task.get(field, '')))}</td>" for field in _TASK_FIELDS
        )
        rows.append(f"<tr>{cells}</tr>")
    body_rows = "\n".join(rows) if rows else (
        '<tr><td colspan="6">No active tasks.</td></tr>'
    )
    generated_line = (
        f'<p class="meta">Generated at {html.escape(generated)}</p>'
        if generated
        else ""
    )
    headers = "".join(f"<th>{html.escape(field)}</th>" for field in _TASK_FIELDS)
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Board snapshot</title>
<style>
body {{ font-family: Georgia, "Times New Roman", serif; margin: 2rem; color: #222; background: #f7f5f0; }}
h1 {{ font-size: 1.5rem; margin: 0 0 0.5rem; }}
.meta {{ color: #555; margin: 0 0 1.25rem; }}
ul.counts {{ list-style: none; padding: 0; display: flex; flex-wrap: wrap; gap: 0.75rem 1.25rem; margin: 0 0 1.5rem; }}
ul.counts .status {{ font-weight: bold; }}
table {{ border-collapse: collapse; width: 100%; background: #fff; }}
th, td {{ border: 1px solid #ccc; padding: 0.4rem 0.6rem; text-align: left; vertical-align: top; }}
th {{ background: #ebe6dc; }}
</style>
</head>
<body>
<h1>Board snapshot</h1>
{generated_line}
<ul class="counts">
{count_items}
</ul>
<table>
<thead><tr>{headers}</tr></thead>
<tbody>
{body_rows}
</tbody>
</table>
</body>
</html>
"""


def dumps_board_json(payload: dict[str, Any]) -> str:
    """Serialize with sorted keys so byte-identical re-runs stay stable."""
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2) + "\n"


def _write_bytes(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists() and path.read_bytes() == data:
        return
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_bytes(data)
    tmp.replace(path)


def write_board_snapshot(
    data_dir: Path | str,
    db: Session,
    *,
    now: dt.datetime | None = None,
) -> dict[str, Path]:
    """Write ``board.json`` and ``board.html`` under ``<data-dir>/snapshots/``."""
    root = Path(data_dir)
    out_dir = root / SNAPSHOT_DIRNAME
    out_dir.mkdir(parents=True, exist_ok=True)

    payload = build_board_payload(db, now=now)
    json_text = dumps_board_json(payload)
    html_text = render_board_html(payload)

    json_path = out_dir / JSON_FILENAME
    html_path = out_dir / HTML_FILENAME
    _write_bytes(json_path, json_text.encode("utf-8"))
    _write_bytes(html_path, html_text.encode("utf-8"))
    return {"json": json_path, "html": html_path, "dir": out_dir}


