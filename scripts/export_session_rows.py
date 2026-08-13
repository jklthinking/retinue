#!/usr/bin/env python3
"""Emit redacted session rows for a central Retinue collector over SSH."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from adapters.exporters.sessions import collect_sessions


def parse_source(value: str) -> tuple[str, Path]:
    runtime, separator, raw_path = value.partition("=")
    if not separator or not runtime or not raw_path:
        raise argparse.ArgumentTypeError("source must be RUNTIME=/absolute/path")
    return runtime, Path(raw_path)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--actor", required=True)
    parser.add_argument("--source", action="append", type=parse_source, default=[])
    parser.add_argument("--limit", type=int, default=500)
    args = parser.parse_args()
    rows = []
    results = []
    failed = False
    for runtime, source in args.source:
        if not source.is_dir():
            results.append({"runtime": runtime, "status": "skipped"})
            continue
        try:
            batch = collect_sessions(
                source,
                runtime=runtime,
                agent_id=args.actor,
                privacy="summary",
                limit=max(1, min(args.limit, 500)),
            )
            rows.extend(batch)
            results.append({"runtime": runtime, "status": "ok", "count": len(batch)})
        except Exception as exc:
            failed = True
            results.append(
                {"runtime": runtime, "status": "error", "reason": str(exc)[:240]}
            )
    print(json.dumps({"rows": rows, "results": results}, ensure_ascii=False))
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
