#!/usr/bin/env python3
"""Collect redacted session summaries from internal nodes and push centrally."""

from __future__ import annotations

import argparse
import datetime as dt
import json
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from adapters.exporters.sessions import collect_sessions
from server.push_sessions import push


def local_rows(target: dict) -> dict:
    rows = []
    details = []
    for item in target.get("sources", []):
        runtime = item["runtime"]
        source = Path(item["path"])
        if not source.is_dir():
            details.append({"runtime": runtime, "status": "skipped"})
            continue
        batch = collect_sessions(
            source,
            runtime=runtime,
            agent_id=target["actor"],
            privacy="summary",
            limit=500,
        )
        rows.extend(batch)
        details.append({"runtime": runtime, "status": "ok", "count": len(batch)})
    return {"rows": rows, "results": details}


def remote_rows(target: dict) -> dict:
    command = [
        "ssh",
        "-o",
        "BatchMode=yes",
        "-o",
        "ConnectTimeout=20",
        target["host"],
        target.get("collector", "/opt/retinue-session-sync/scripts/export_session_rows.py"),
        "--actor",
        target["actor"],
    ]
    for item in target.get("sources", []):
        command.extend(["--source", f"{item['runtime']}={item['path']}"])
    completed = subprocess.run(
        command,
        text=True,
        capture_output=True,
        timeout=180,
        check=False,
    )
    if completed.returncode not in {0, 1} or not completed.stdout.strip():
        raise RuntimeError(
            f"remote collector failed ({completed.returncode}): "
            f"{completed.stderr.strip()[:240]}"
        )
    return json.loads(completed.stdout)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, required=True)
    args = parser.parse_args()
    config = json.loads(args.config.read_text(encoding="utf-8"))
    results = []
    failed = False
    for target in config["targets"]:
        try:
            exported = remote_rows(target) if target.get("host") else local_rows(target)
            token = Path(target["token_file"]).read_text(encoding="utf-8").strip()
            synced = push(config["url"], token, exported["rows"])
            results.append(
                {
                    "actor": target["actor"],
                    "host": target.get("host") or "local",
                    "status": "ok",
                    "sources": exported.get("results", []),
                    **synced,
                }
            )
        except Exception as exc:
            failed = True
            results.append(
                {
                    "actor": target["actor"],
                    "host": target.get("host") or "local",
                    "status": "error",
                    "reason": str(exc)[:300],
                }
            )
    print(
        json.dumps(
            {
                "at": dt.datetime.now().astimezone().isoformat(timespec="seconds"),
                "results": results,
            },
            ensure_ascii=False,
        )
    )
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
