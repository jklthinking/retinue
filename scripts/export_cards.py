"""Export server-mode task cards to the protocol file shape, one-way.

The server shape keeps cards in its database: it has queries and an
append-only chain, but nothing an operator can ``git log``. The file shape
keeps cards as YAML under ``tasks/`` and gets history, blame, and diffs from
any repository. This script writes the database cards out in that file shape
so the two can coexist: SQL for queries, git for audit.

The boundary is strict and deliberate:

- One-way. It reads the database and writes the output directory. It never
  reads an exported file back and never writes to the database, so the
  export cannot quietly become a sync with two sources of truth.
- Deterministic. Cards come out in id order with a fixed field order,
  ``depends_on`` sorted, and the chain in recorded ``seq`` order. No
  export-time timestamps are embedded, so running it twice against an
  unchanged database produces byte-identical files and a clean diff.
- Git-agnostic. It never invokes git. Whether the output directory is a
  repository, and when to commit, is the operator's business. The summary
  prints a suggested commit message as a convenience only.
- Privacy-scoped. Only what the file protocol already validates is emitted:
  the card fields and the ``task`` event chain. Session bodies, token
  hashes, node topology, attempts, review/governance payloads, and
  server-only bookkeeping stay in the database.

Usage:
    python scripts/export_cards.py --data-dir ./retinue-data --out path/to/repo/tasks
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import yaml
from sqlalchemy import select
from sqlalchemy.orm import Session

from core.protocol.task import (
    ProtocolError,
    validate_dependency_graph,
    validate_task,
)
from server.db import Task, TaskDependency, TaskEvent, make_session_factory

# Fixed field order, mirroring what core.protocol.task.create_task writes, so
# exported cards are diff-stable against file-mode cards.
CARD_FIELD_ORDER = (
    "id",
    "title",
    "created_by",
    "dept",
    "priority",
    "acceptance",
    "depends_on",
    "status",
    "holder",
    "blocked_reason",
    "chain",
    "next",
    "refs",
)
EVENT_FIELD_ORDER = (
    "who",
    "did",
    "at",
    "from_status",
    "to_status",
    "from_holder",
    "to_holder",
)


def build_cards(db: Session) -> dict[str, dict[str, Any]]:
    """Read the database once and render every card in the file shape."""
    tasks = list(db.execute(select(Task).order_by(Task.id)).scalars())

    dependencies: dict[str, list[str]] = {}
    edges = db.execute(
        select(TaskDependency)
        .where(TaskDependency.kind == "blocks")
        .order_by(TaskDependency.dependent_id, TaskDependency.prerequisite_id)
    ).scalars()
    for edge in edges:
        dependencies.setdefault(edge.dependent_id, []).append(edge.prerequisite_id)

    # Only protocol chain events belong on a card. Review, reply, and
    # governance events are server-side records with payloads the file shape
    # does not carry, so they stay behind.
    chains: dict[str, list[dict[str, Any]]] = {}
    events = db.execute(
        select(TaskEvent)
        .where(TaskEvent.event_type == "task")
        .order_by(TaskEvent.task_id, TaskEvent.seq)
    ).scalars()
    for event in events:
        chains.setdefault(event.task_id, []).append(
            {field: getattr(event, field) for field in EVENT_FIELD_ORDER}
        )

    cards: dict[str, dict[str, Any]] = {}
    for task in tasks:
        card = {
            "id": task.id,
            "title": task.title,
            "created_by": task.created_by,
            "dept": task.dept or None,
            "priority": task.priority,
            "acceptance": task.acceptance,
            "depends_on": dependencies.get(task.id, []),
            "status": task.status,
            "holder": task.holder,
            "blocked_reason": task.blocked_reason,
            "chain": chains.get(task.id, []),
            "next": task.next_holder or None,
            "refs": task.refs,
        }
        # Fail loudly if server data cannot satisfy the protocol: loosening
        # the lint to accommodate the export is explicitly not an option.
        validate_task(card)
        cards[task.id] = card
    validate_dependency_graph(cards)
    return cards


def _write_card(path: Path, card: dict[str, Any]) -> None:
    """Atomically write one card, byte-compatible with the file protocol."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        yaml.safe_dump(card, sort_keys=False, allow_unicode=True), encoding="utf-8"
    )
    temporary.replace(path)


def export_cards(db: Session, out_dir: Path) -> dict[str, Any]:
    """Write every card to ``out_dir`` and describe what happened."""
    cards = build_cards(db)
    out_dir.mkdir(parents=True, exist_ok=True)
    for task_id, card in cards.items():
        _write_card(out_dir / f"{task_id}.yaml", card)
    # Cards whose files already exist but are gone from the database are left
    # untouched and only reported: deleting from an operator's repository is
    # not this tool's call. Names come from the directory listing alone; the
    # exported files themselves are never read back.
    stale = sorted(
        candidate.name
        for pattern in ("*.yaml", "*.yml")
        for candidate in out_dir.glob(pattern)
        if candidate.stem not in cards
    )
    return {
        "out": str(out_dir),
        "cards": len(cards),
        "written": sorted(cards),
        "stale": stale,
        "suggested_commit_message": f"export server cards: {len(cards)} card(s)",
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Export server-mode task cards to the YAML file shape, one-way. "
            "Reads the database only; never invokes git."
        )
    )
    parser.add_argument(
        "--data-dir",
        default="./retinue-data",
        help="server data directory containing retinue.db",
    )
    parser.add_argument(
        "--out",
        required=True,
        type=Path,
        help="directory to receive one <id>.yaml per card (e.g. tasks/ in a repo)",
    )
    args = parser.parse_args(argv)

    db_path = Path(args.data_dir) / "retinue.db"
    if not db_path.is_file():
        print(f"export failed: database not found: {db_path}", file=sys.stderr)
        return 1
    factory = make_session_factory(db_path)
    try:
        with factory() as db:
            result = export_cards(db, args.out)
    except ProtocolError as exc:
        print(f"export failed: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
