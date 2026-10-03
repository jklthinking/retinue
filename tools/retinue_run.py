"""Controlled launch boundary for trusted runtime adapters.

Worker identity remains the existing device/model actor. This helper does not
read commands from task text, claim cards, create credentials, or retry a launch.
Adapters supply their existing configured launcher and a matching stop callback.
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
import hashlib
from typing import Any
from uuid import uuid4

from tools.retinue_worker import WorkerError, api_call


class LaunchDenied(WorkerError):
    """This run has consumed its launch grant, or preflight is incompatible."""


@dataclass(frozen=True)
class ControlledRun:
    task_id: str
    run_id: str
    lease_term: int
    context: dict[str, Any]
    handle: Any


def start_controlled_run(
    base_url: str,
    token: str,
    task_id: str,
    lease_term: int,
    *,
    title: str,
    launch: Callable[[dict[str, Any]], Any],
    stop: Callable[[Any], None],
    model: str | None = None,
    runtime_session_id: int | None = None,
    request_key: str | None = None,
    module: str | None = None,
    before_launch: Callable[[dict[str, Any]], None] | None = None,
) -> ControlledRun:
    """Read context, prepare, consume a grant, then launch exactly once.

    The launch callback receives task data, never executable configuration.
    It must not return until the runtime actually started. It must clean up
    partial startup itself if it raises. The stop callback terminates that exact
    returned handle if Retinue rejects the started receipt. A transport error at
    any boundary fails closed; the helper never retries or grants a second start.
    """
    key = request_key or f"adapter:{uuid4().hex}"
    context = api_call(base_url, token, "GET", f"/api/tasks/{task_id}/context")
    if (not isinstance(context, dict) or context.get("version") != 1
            or context.get("task_id") != task_id):
        raise LaunchDenied("context packet is incompatible with this task")
    body = {"title": title, "lease_term": lease_term, "idempotency_key": key,
            "execution_state": "prepared"}
    if model is not None:
        body["model"] = model
    if runtime_session_id is not None:
        body["runtime_session_id"] = runtime_session_id
    if module is not None:
        body["module"] = module
    prepared = api_call(base_url, token, "POST", f"/api/tasks/{task_id}/runs", body)
    run_id = prepared["run"]["id"]
    granted = api_call(base_url, token, "POST",
                       f"/api/tasks/{task_id}/runs/{run_id}/authorize-execution",
                       {"lease_term": lease_term, "request_key": key})
    if granted.get("allow_execute") is not True:
        raise LaunchDenied("launch permission already consumed; do not restart this run")
    if before_launch is not None:
        before_launch({"task_id": task_id, "run_id": run_id, "lease_term": lease_term,
                       "prepared_at": prepared["run"].get("prepared_at")})
    handle = launch(context)
    try:
        api_call(base_url, token, "POST", f"/api/tasks/{task_id}/runs/{run_id}/events",
                 {"lease_term": lease_term, "idempotency_key": "started:" + hashlib.sha256(key.encode()).hexdigest(),
                  "status": "running", "execution_state": "started",
                  "note": "Runtime adapter reports successful startup"})
    except Exception:
        stop(handle)
        raise
    return ControlledRun(task_id, run_id, lease_term, context, handle)


def report_progress(base_url: str, token: str, run: ControlledRun, *,
                    note: str, completed: list[dict[str, Any]], remaining: list[str],
                    next_action: str | None = None, next_owner: str | None = None) -> dict[str, Any]:
    """Report structured executor claims; this does not grant acceptance or ownership."""
    return api_call(base_url, token, "POST",
                    f"/api/tasks/{run.task_id}/runs/{run.run_id}/events",
                    {"lease_term": run.lease_term, "idempotency_key": f"progress:{uuid4().hex}",
                     "status": "running", "note": note,
                     "progress_report": {"completed": completed, "remaining": remaining,
                                         "next_action": next_action, "next_owner": next_owner}})
