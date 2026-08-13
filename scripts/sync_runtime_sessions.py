#!/usr/bin/env python3
"""Synchronize local runtime conversations into Retinue without mutating them."""

from __future__ import annotations

import argparse
import datetime as dt
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from server.push_sessions import push_sessions


def parse_source(value: str) -> tuple[str, Path]:
    runtime, separator, raw_path = value.partition("=")
    if not separator or not runtime or not raw_path:
        raise argparse.ArgumentTypeError("source must be RUNTIME=/absolute/path")
    return runtime, Path(raw_path).expanduser()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--actor", required=True)
    parser.add_argument("--url", required=True)
    parser.add_argument("--token-file", type=Path, required=True)
    parser.add_argument("--source", action="append", type=parse_source, default=[])
    parser.add_argument(
        "--privacy", choices=("metadata", "summary", "full"), default="summary"
    )
    parser.add_argument("--limit", type=int, default=500)
    args = parser.parse_args()

    token = args.token_file.read_text(encoding="utf-8").strip()
    results = []
    failed = False
    for runtime, source in args.source:
        if not source.is_dir():
            results.append(
                {"runtime": runtime, "source": str(source), "status": "skipped"}
            )
            continue
        try:
            result = push_sessions(
                runtime=runtime,
                source=str(source),
                actor_id=args.actor,
                url=args.url,
                token=token,
                privacy=args.privacy,
                limit=max(1, min(args.limit, 500)),
            )
            results.append(
                {"runtime": runtime, "source": str(source), "status": "ok", **result}
            )
        except Exception as exc:
            failed = True
            results.append(
                {
                    "runtime": runtime,
                    "source": str(source),
                    "status": "error",
                    "reason": str(exc)[:300],
                }
            )
    print(
        json.dumps(
            {
                "at": dt.datetime.now().astimezone().isoformat(timespec="seconds"),
                "actor": args.actor,
                "privacy": args.privacy,
                "results": results,
            },
            ensure_ascii=False,
        )
    )
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
