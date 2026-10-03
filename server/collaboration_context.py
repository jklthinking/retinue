"""Read-only handoff packets from task evidence, never runtime conversation text."""

from __future__ import annotations

import copy
import hashlib
import json
from typing import Any

from sqlalchemy.orm import Session

from .collaboration import TERMINAL_RUNS, collaboration_projection
from .db import RuntimeSession, Task
from .deps import Principal
from .discovery import canonical_runtime
from .engine import Forbidden


def _context_run(db: Session, source: dict[str, Any], principal: Principal) -> dict[str, Any]:
    run = copy.deepcopy(source)
    session_id = run.pop("runtime_session_id", None)
    # A free-form legacy reference is not a verified session binding and can
    # contain a native session identifier; it has no place in a shared packet.
    run.pop("session_ref", None)
    run["session_link"] = None
    if session_id is not None:
        row = db.get(RuntimeSession, session_id)
        allowed = principal.privileged or (
            principal.kind == "agent" and principal.actor_id == run["actor_id"])
        if (allowed and row is not None and row.actor_id == run["actor_id"]
                and row.task_id == run["task_id"]
                and canonical_runtime(row.runtime) == canonical_runtime(run.get("runtime") or "")
                and row.node == run.get("node")):
            run["session_link"] = {"id": row.id, "access": "permitted"}
    return run


def task_context(db: Session, task: Task, principal: Principal) -> dict[str, Any]:
    """Compact shared memory whose assertions preserve their evidence source.

    Confirmation below means a recorded task-state transition, not independently
    verified artifact contents. Executor progress always remains a claim.
    """
    if principal.kind == "channel":
        raise Forbidden("channel credentials cannot read task context")
    view = collaboration_projection(db, task, principal)
    runs = [_context_run(db, run, principal) for run in view["runs"]]
    next_steps, claims, evidence, confirmed = [], [], [], []
    for run in runs:
        work = run.get("progress_report") or {}
        if work or run.get("progress") is not None:
            claims.append({"task_id": run["task_id"], "run_id": run["id"],
                           "actor_id": run["actor_id"], "completed": work.get("completed", []),
                           "progress": run.get("progress"),
                           "reported_at": run.get("progress_reported_at"),
                           "verification": "unverified", "freshness": run["freshness"]})
        for item in work.get("completed", []):
            if item.get("refs") or item.get("revision"):
                evidence.append({"task_id": run["task_id"], "run_id": run["id"],
                                 "summary": item["summary"], "refs": item.get("refs", []),
                                 "revision": item.get("revision"), "source": "executor_report",
                                 "verification": "unverified"})
        if run.get("refs"):
            evidence.append({"task_id": run["task_id"], "run_id": run["id"],
                             "summary": run["latest_note"], "refs": run["refs"],
                             "revision": None, "source": "executor_report", "verification": "unverified"})
        if run["status"] not in TERMINAL_RUNS:
            next_steps.append({"task_id": run["task_id"], "run_id": run["id"],
                               "action": work.get("next_action"), "owner": work.get("next_owner"),
                               "remaining": work.get("remaining", []), "waiting": run.get("waiting"),
                               "source": "executor_report", "grants_authority": False})
    for item in view["tasks"]:
        # Don't circulate channel user metadata through a shared handoff packet.
        item.pop("source_user", None)
        item.pop("source_channel", None)
        if item["status"] == "done":
            event = next((event for event in reversed(view["events"])
                          if event["task_id"] == item["id"] and
                          event["payload"].get("changes", {}).get("status", {}).get("after") == "done"), None)
            if event:
                confirmed.append({"task_id": item["id"], "status": "done",
                                  "event_id": event["id"], "at": event["at"], "who": event["who"],
                                  "confirmation": "recorded_task_state", "acceptance_verified": False})
        if item.get("refs"):
            evidence.append({"task_id": item["id"], "run_id": None, "summary": item["title"],
                             "refs": item["refs"], "revision": None,
                             "source": "task_record", "verification": "unverified"})
    packet = {
        "version": 1, "task_id": task.id, "root_task_id": view["root_task_id"],
        "generated_at": view["generated_at"],
        "revision": {**view["cursor"], "task_updated_at": task.updated_at.isoformat()},
        "task": next(item for item in view["tasks"] if item["id"] == task.id),
        "collaboration": {key: view[key] for key in ("tasks", "delegations", "coverage")},
        "runs": runs,
        "active_runs": [run for run in runs if run["status"] not in TERMINAL_RUNS
                        and run["lease_current"] and run["lease_live"]
                        and run.get("execution_state") == "started"],
        "confirmed_progress": confirmed, "unverified_claims": claims,
        "attempts": [{key: value for key, value in attempt.items()
                      if key not in {"session_ref", "checkpoint_ref"}} for attempt in view["attempts"]],
        "evidence": evidence, "next": next_steps,
        "privacy": {"contains_private_conversations": False, "contains_session_summaries": False,
                    "session_links": "owner_or_operator_only"},
    }
    # A content hash identifies exactly this caller-visible packet even when an
    # old snapshot includes concurrent writes beyond the incremental feed ceiling.
    payload = {key: value for key, value in packet.items() if key not in {"generated_at", "revision"}}
    packet["revision"]["content_hash"] = hashlib.sha256(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    return packet
