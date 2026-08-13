#!/usr/bin/env python3
"""Generic worker that claims a card, heartbeats the lease, and reports attempts.

This is the orchestration counterpart of the isolated teacher worker: it talks
only HTTP, never invents on_claim commands from card text, and stops writing
when the server fences a stale lease term.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from server.http_client import RequestClass, open_url


LOG = logging.getLogger("retinue.worker")
DEFAULT_HEARTBEAT_SECONDS = 15
TRANSIENT_HINTS = (
    "disconnect",
    "timed out",
    "timeout",
    "stuck",
    "connection reset",
)
SEMANTIC_HINTS = (
    "quota",
    "credential",
    "context overflow",
    "context-overflow",
    "configuration",
)


class WorkerError(RuntimeError):
    pass


class FencedWrite(WorkerError):
    """The server rejected this worker's lease term."""


def api_call(
    base_url: str,
    token: str,
    method: str,
    path: str,
    body: dict[str, Any] | None = None,
) -> Any:
    request = urllib.request.Request(
        base_url.rstrip("/") + path,
        method=method,
        data=json.dumps(body, ensure_ascii=False).encode("utf-8")
        if body is not None
        else None,
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        },
    )
    try:
        with open_url(request, timeout=20, request_class=RequestClass.INWARD) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        if exc.code == 409 and "lease" in detail.lower():
            raise FencedWrite(f"{method} {path}: HTTP {exc.code} {detail}") from exc
        raise WorkerError(f"{method} {path}: HTTP {exc.code} {detail}") from exc
    except urllib.error.URLError as exc:
        raise WorkerError(f"{method} {path}: {exc.reason}") from exc


def read_token(token_path: Path) -> str:
    if not token_path.is_file():
        raise WorkerError("missing token file")
    return token_path.read_text(encoding="utf-8").strip()


def classify_failure(message: str) -> str:
    text = message.lower()
    if any(hint in text for hint in SEMANTIC_HINTS):
        return "semantic"
    if any(hint in text for hint in TRANSIENT_HINTS):
        return "transient"
    return "transient"


def lease_term_of(task: dict[str, Any]) -> int:
    lease = task.get("lease") or {}
    try:
        return int(lease.get("term") or 0)
    except (TypeError, ValueError):
        return 0


def claim_ready_task(
    base_url: str, token: str, task_id: str, note: str = "worker claim"
) -> dict[str, Any]:
    return api_call(
        base_url, token, "POST", f"/api/tasks/{task_id}/claim", {"note": note}
    )


def heartbeat_once(
    base_url: str,
    token: str,
    task_id: str,
    lease_term: int,
    *,
    started: bool = False,
    workdir_key: str | None = None,
) -> dict[str, Any]:
    body: dict[str, Any] = {"lease_term": lease_term, "started": started}
    if workdir_key:
        body["workdir_key"] = workdir_key
    return api_call(base_url, token, "POST", f"/api/tasks/{task_id}/heartbeat", body)


def report_attempt(
    base_url: str,
    token: str,
    task_id: str,
    *,
    outcome: str,
    started_at: str,
    ended_at: str,
    lease_term: int,
    trigger_source: str = "worker",
    reason: str | None = None,
    exit_status: int | None = None,
    session_ref: str | None = None,
    checkpoint_ref: str | None = None,
    failure_class: str | None = None,
    workdir_key: str | None = None,
) -> dict[str, Any]:
    body: dict[str, Any] = {
        "outcome": outcome,
        "started_at": started_at,
        "ended_at": ended_at,
        "idempotency_key": f"worker:{uuid.uuid4()}",
        "lease_term": lease_term,
        "trigger_source": trigger_source,
    }
    if reason is not None:
        body["reason"] = reason
    if exit_status is not None:
        body["exit_status"] = exit_status
    if session_ref:
        body["session_ref"] = session_ref
    if checkpoint_ref:
        body["checkpoint_ref"] = checkpoint_ref
    if failure_class:
        body["failure_class"] = failure_class
    if workdir_key:
        body["workdir_key"] = workdir_key
    return api_call(base_url, token, "POST", f"/api/tasks/{task_id}/attempts", body)


def precheck_acceptance(
    base_url: str,
    token: str,
    task_id: str,
    lease_term: int,
    checks: list[dict[str, Any]],
) -> dict[str, Any]:
    return api_call(
        base_url,
        token,
        "POST",
        f"/api/tasks/{task_id}/precheck",
        {"lease_term": lease_term, "checks": checks},
    )


def poll_ready(base_url: str, token: str) -> list[dict[str, Any]]:
    payload = api_call(base_url, token, "GET", "/api/tasks/ready")
    if isinstance(payload, list):
        return payload
    return list(payload.get("items") or [])


def run_once(args: argparse.Namespace) -> int:
    token = read_token(Path(args.token_file))
    ready = poll_ready(args.server_url, token)
    claimed = 0
    for card in ready:
        if not card.get("open_dispatch") and card.get("status") != "blocked":
            continue
        try:
            task = claim_ready_task(args.server_url, token, card["id"])
        except WorkerError as exc:
            LOG.info("skip %s: %s", card["id"], exc)
            continue
        term = lease_term_of(task)
        if term <= 0:
            raise WorkerError("claim did not return a lease term")
        heartbeat_once(
            args.server_url,
            token,
            task["id"],
            term,
            started=True,
            workdir_key=args.workdir_key,
        )
        claimed += 1
        if args.once:
            break
    return claimed


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--server-url", default="http://127.0.0.1:8787")
    parser.add_argument("--token-file", required=True)
    parser.add_argument("--workdir-key", default=None)
    parser.add_argument("--heartbeat-seconds", type=int, default=DEFAULT_HEARTBEAT_SECONDS)
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    if args.once:
        count = run_once(args)
        LOG.info("claimed %s card(s)", count)
        return 0
    while True:
        try:
            run_once(args)
        except FencedWrite:
            LOG.error("fenced; stopping")
            return 2
        except WorkerError:
            LOG.exception("worker cycle failed")
        time.sleep(max(1, args.heartbeat_seconds))


if __name__ == "__main__":
    raise SystemExit(main())
