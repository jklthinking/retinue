#!/usr/bin/env python3
"""List and settle queued vault captures over an authenticated SSH channel."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from sqlalchemy import select
from server.db import RuntimeSession, SessionCapture, make_session_factory, utcnow


def list_items(db_path: Path, limit: int) -> int:
    factory = make_session_factory(db_path)
    with factory() as db:
        rows = db.execute(
            select(SessionCapture, RuntimeSession)
            .join(RuntimeSession, RuntimeSession.id == SessionCapture.session_id)
            .where(SessionCapture.status == "queued")
            .order_by(SessionCapture.id.asc())
            .limit(max(1, min(limit, 100)))
        ).all()
        items = [
            {
                "id": capture.id,
                "session_id": capture.session_id,
                "actor_id": capture.actor_id,
                "kind": capture.kind,
                "title": capture.title,
                "markdown": capture.markdown,
                "runtime": session.runtime,
                "node": session.node,
                "updated_at": (
                    session.updated_at.isoformat() if session.updated_at else ""
                ),
            }
            for capture, session in rows
        ]
    print(json.dumps(items, ensure_ascii=False))
    return 0


def mark_items(db_path: Path) -> int:
    payload = json.load(sys.stdin)
    if not isinstance(payload, list):
        raise SystemExit("mark payload must be a list")
    factory = make_session_factory(db_path)
    updated = 0
    with factory() as db:
        for item in payload:
            capture = db.get(SessionCapture, int(item["id"]))
            target = str(item["target_path"])
            if (
                capture is None
                or capture.status != "queued"
                or not (
                    target.startswith("40_Commons/话题归档/")
                    or target.startswith("00_Inbox/01_输入/会话归档/")
                )
            ):
                continue
            capture.status = "exported"
            capture.target_path = target
            capture.exported_at = utcnow()
            updated += 1
        db.commit()
    print(json.dumps({"updated": updated}))
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=("list", "mark"))
    parser.add_argument(
        "--db",
        type=Path,
        required=True,
        help="path to the Retinue database",
    )
    parser.add_argument("--limit", type=int, default=50)
    args = parser.parse_args()
    return (
        list_items(args.db, args.limit)
        if args.command == "list"
        else mark_items(args.db)
    )


if __name__ == "__main__":
    raise SystemExit(main())
