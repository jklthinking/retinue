"""Task-scoped collaboration backed by the existing append-only event chain.

Delegation creates a separately held card, never gives a peer authority over
its parent's card. Runs are reported execution evidence, not another task state
machine. A retry prepares the existing lease; it does not start a process.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
from typing import Any

from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from core.protocol.task import ProtocolError, validate_ledger_text

from .collaboration_schemas import DelegationBody, DelegationPolicyBody, RunCreateBody, RunEventBody
from .db import Actor, RuntimeSession, Task, TaskAttempt, TaskEvent, utcnow
from .deps import Principal
from .discovery import canonical_runtime, is_sync_actor, model_identity_state
from .engine import (
    Conflict,
    Forbidden,
    LeaseSettings,
    _require_actor,
    append_annotation_event,
    assert_lease_write,
    attempt_to_dict,
    create_task,
    dependency_graph,
    lease_is_live,
    lease_settings,
    retry_plan,
    retry_task,
    task_summary_to_dict,
)

TERMINAL_RUNS = {"succeeded", "failed", "cancelled"}
PROGRESS_STALE_AFTER_SECONDS = 900
RUN_TRANSITIONS = {
    "running": {"running", "waiting", *TERMINAL_RUNS},
    "waiting": {"waiting", "running", "failed", "cancelled"},
}


def payload_of(event: TaskEvent) -> dict[str, Any]:
    try:
        value = json.loads(event.payload_json or "{}")
    except (ValueError, TypeError):
        return {}
    return value if isinstance(value, dict) else {}


def ancestry(task: Task) -> dict[str, Any]:
    if task.events:
        value = payload_of(task.events[0]).get("collaboration")
        if isinstance(value, dict) and value.get("version") == 1:
            return value
    return {"version": 1, "root_task_id": task.id, "parent_task_id": None,
            "delegation_id": None, "parent_run_id": None}


def _serialize_write(db: Session, task: Task) -> None:
    """Serialize checks and writes across this tree, including on SQLite.

    SELECT FOR UPDATE is ignored by SQLite. A no-op update acquires its writer
    lock (and a root-row lock on other engines) without changing the task or
    its updated_at. Expiring loaded snapshots after the lock prevents two
    previously loaded sessions from both consuming the last grant/run slot.
    """
    root_id = ancestry(task)["root_task_id"]
    db.flush()
    db.execute(text("UPDATE tasks SET id = id WHERE id = :root_id"), {"root_id": root_id})
    db.expire_all()
    db.refresh(task)


def _key(prefix: str, task_id: str, who: str, value: str) -> str:
    digest = hashlib.sha256(f"{prefix}\0{task_id}\0{who}\0{value}".encode()).hexdigest()[:32]
    return f"{prefix}-{digest}"


def _assert_writer(task: Task, principal: Principal, lease_term: int | None) -> None:
    if principal.kind == "channel" or principal.role == "viewer":
        raise Forbidden("collaboration reports require a holder or operator")
    if not principal.privileged and task.holder != principal.write_identity:
        raise Forbidden(f"holder-only-writes: {principal.write_identity!r} does not hold {task.id}")
    # Unlike an interactive progress update, an execution report never remints
    # an expired lease. The executor must explicitly renew/start and open a new run.
    assert_lease_write(task, lease_term, is_privileged=principal.privileged)


def _existing(db: Session, key: str, request: dict[str, Any]) -> TaskEvent | None:
    event = db.scalar(select(TaskEvent).where(TaskEvent.event_key == key))
    if event is not None and _module_defaults(payload_of(event).get("request")) != _module_defaults(request):
        raise Conflict("idempotency key already used for a different collaboration report")
    return event


def _module_defaults(value: Any) -> Any:
    """Optional module metadata must not invalidate old idempotent requests."""
    if isinstance(value, dict):
        return {key: _module_defaults(item) for key, item in value.items()
                if key not in {"module", "pipeline"} or item is not None}
    if isinstance(value, list):
        return [_module_defaults(item) for item in value]
    return value


def _require_execution_actor(db: Session, actor_id: str, field: str) -> Actor:
    """Keep session-index transports out of model work without requiring a model label."""
    actor = _require_actor(db, actor_id, field)
    if is_sync_actor(actor):
        raise Forbidden("session sync agents cannot receive or execute collaboration work")
    return actor


def _heartbeat_live_lease(task: Task, *, now: dt.datetime, settings: LeaseSettings) -> None:
    """Refresh an existing live lease; never revive expired execution authority."""
    if not lease_is_live(task, now):
        return
    task.lease_heartbeat_at = now
    task.lease_expires_at = now + dt.timedelta(seconds=settings.lost_seconds)


def _event(db: Session, task: Task, principal: Principal, *, key: str,
           kind: str, note: str, data: dict[str, Any], request: dict[str, Any]) -> TaskEvent:
    if lease_is_live(task):
        _heartbeat_live_lease(task, now=utcnow(), settings=lease_settings())
    return append_annotation_event(
        db, task, who=principal.write_identity, did=note, event_type=kind,
        event_key=key, payload={"collaboration_version": 1, **data, "request": request,
                                "reporter": {"kind": principal.kind, "id": principal.name}},
    )


def policy_of(task: Task) -> dict[str, Any] | None:
    policy = None
    for event in task.events:
        value = payload_of(event).get("delegation_policy")
        if event.event_type == "delegation_policy" and isinstance(value, dict):
            policy = value
    return policy


def set_delegation_policy(db: Session, task: Task, principal: Principal,
                          body: DelegationPolicyBody) -> dict[str, Any]:
    if not principal.privileged:
        raise Forbidden("only an operator may authorise delegation")
    _serialize_write(db, task)
    if ancestry(task).get("parent_task_id"):
        raise ProtocolError("delegation policy belongs to the root task")
    if task.status in {"done", "cancelled"}:
        raise ProtocolError("terminal tasks cannot change delegation policy")
    request = body.model_dump(mode="json")
    key = _key("delegation-policy", task.id, principal.write_identity, body.idempotency_key)
    existing = _existing(db, key, request)
    if existing is not None:
        return {"created": False, "policy": payload_of(existing)["delegation_policy"]}
    for actor_id in body.allowed_actor_ids:
        actor = _require_execution_actor(db, actor_id, "allowed_actor_ids")
        if actor.kind != "agent":
            raise ProtocolError("agent delegation policy can only name agent actors")
    policy = body.model_dump(exclude={"idempotency_key"})
    _event(db, task, principal, key=key, kind="delegation_policy",
           note="Operator updated the bounded delegation policy",
           data={"delegation_policy": policy}, request=request)
    return {"created": True, "policy": policy}


def delegation_capability(db: Session, task: Task, principal: Principal) -> dict[str, Any]:
    root_id, tasks, _ = task_tree(db, task)
    root = tasks[0]
    policy = policy_of(root)
    targets: list[str] = []
    available, reason = True, "已建立子卡后，等待执行者开工。"
    try:
        _assert_writer(task, principal, None)
        _require_execution_actor(db, task.holder, "delegating actor")
        if task.status in {"done", "cancelled"} or root.status in {"done", "cancelled"}:
            raise ProtocolError("终态任务不能新增委派。")
        limits = policy or {"max_depth": 3, "max_children": 12}
        if len(tasks) - 1 >= limits["max_children"]:
            raise ProtocolError("此根任务已达到子卡数量上限。")
        depth, current, by_id = 0, task, {item.id: item for item in tasks}
        while ancestry(current).get("parent_task_id"):
            depth += 1
            current = by_id[ancestry(current)["parent_task_id"]]
        if depth >= limits["max_depth"]:
            raise ProtocolError("此分支已达到委派深度上限。")
        enabled = [actor for actor in db.scalars(select(Actor).where(Actor.disabled.is_(False)).order_by(Actor.id))
                   if not is_sync_actor(actor)]
        if principal.privileged:
            targets = [actor.id for actor in enabled]
        else:
            allowed = (policy or {}).get("allowed_actor_ids", [])
            # Preserve existing self-only creation. Cross-actor authority exists
            # only when an operator explicitly included both actors in this root.
            permitted = set(allowed) if principal.write_identity in allowed else set()
            permitted.add(principal.write_identity)
            targets = [actor.id for actor in enabled if actor.kind == "agent" and actor.id in permitted]
    except ProtocolError as exc:
        available, reason = False, str(exc)
    return {"can_delegate": available, "delegation_reason": reason,
            "delegation_targets": targets,
            "delegation_lease_term": int(task.lease_term) if task.lease_term else None,
            "delegation_policy": policy,
            "can_manage_delegation_policy": principal.privileged and task.id == root_id
            and root.status not in {"done", "cancelled"}}


def delegate(db: Session, task: Task, principal: Principal, body: DelegationBody) -> dict[str, Any]:
    _serialize_write(db, task)
    _assert_writer(task, principal, body.lease_term)
    _require_execution_actor(db, task.holder, "delegating actor")
    request = body.model_dump(mode="json")
    key = _key("delegation", task.id, principal.write_identity, body.idempotency_key)
    existing = _existing(db, key, request)
    if existing is not None:
        return {"created": False, "delegation": payload_of(existing)["delegation"]}
    if task.status in {"done", "cancelled"}:
        raise ProtocolError("terminal cards cannot delegate new work")
    _require_execution_actor(db, body.delegated_to, "delegated_to")
    capability = delegation_capability(db, task, principal)
    if not capability["can_delegate"]:
        raise ProtocolError(capability["delegation_reason"])
    if body.delegated_to not in capability["delegation_targets"]:
        raise Forbidden("cross-actor delegation requires an operator policy naming both actors")
    stages = None
    if body.pipeline:
        from .flow import validate_pipeline
        stages = validate_pipeline(db, [stage.model_dump() for stage in body.pipeline])
        if stages[0]["holder"] != body.delegated_to:
            raise ProtocolError("delegation target must hold the first pipeline stage")
        for stage in stages:
            _require_execution_actor(db, stage["holder"], "pipeline holder")
            if stage["holder"] not in capability["delegation_targets"]:
                raise Forbidden("every pipeline holder requires the existing root delegation scope")
    if body.parent_run_id:
        run = _find_run(task, body.parent_run_id)
        if run["status"] in TERMINAL_RUNS or run["lease_term"] != int(task.lease_term or 0):
            raise Conflict("parent run must be active in the current lease")
    relation = ancestry(task)
    child = create_task(
        db, title=body.title, created_by=principal.write_identity, holder=body.delegated_to,
        dept=task.dept, priority=task.priority, acceptance=body.acceptance,
        note=body.instruction, event_type="collaboration_child",
        event_payload={"collaboration": {
            "version": 1, "root_task_id": relation["root_task_id"],
            "parent_task_id": task.id, "delegation_id": key,
            "parent_run_id": body.parent_run_id,
            "authorised_by": principal.write_identity,
            "module": body.module,
        }},
    )
    child.source_channel, child.source_user = task.source_channel, task.source_user
    if stages is not None:
        child.pipeline_json = json.dumps(stages, ensure_ascii=False)
        child.pipeline_stage = 0
    data = {
        "id": key, "parent_task_id": task.id, "child_task_id": child.id,
        "parent_run_id": body.parent_run_id, "delegated_by": principal.write_identity,
        "delegated_to": body.delegated_to, "title": body.title,
        "instruction": body.instruction, "acceptance": body.acceptance,
        "module": body.module,
        "pipeline": stages,
        "created_at": child.events[0].at,
    }
    _event(db, task, principal, key=key, kind="delegation", note=body.instruction,
           data={"delegation": data}, request=request)
    return {"created": True, "delegation": data}


def runs_of(task: Task) -> list[dict[str, Any]]:
    runs: dict[str, dict[str, Any]] = {}
    relation = ancestry(task)
    for event in task.events:  # TaskEvent.seq order is canonical, never timestamps.
        data = payload_of(event)
        if event.event_type == "run_started" and isinstance(data.get("run"), dict):
            run = dict(data["run"])
            # Legacy events stay unknown; never backfill from today's mutable roster.
            for field in ("node", "runtime", "identity_recorded_at", "runtime_session_id",
                          "progress_report", "progress_reported_at"):
                run.setdefault(field, None)
            for field in ("node_source", "runtime_source"):
                run.setdefault(field, "unknown")
            run.setdefault("identity_complete", False)
            run.setdefault("execution_state", "started")
            run.setdefault("prepared_at", None)
            run.setdefault("last_report_at", event.at)
            run.setdefault("last_reported_by", run.get("reported_by"))
            run.update(parent_task_id=relation.get("parent_task_id"),
                       delegation_id=relation.get("delegation_id"),
                       parent_run_id=relation.get("parent_run_id"))
            runs[run["id"]] = run
        elif event.event_type == "run_reported" and data.get("run_id") in runs:
            run = runs[data["run_id"]]
            report = data["report"]
            previous_wait = run.get("waiting")
            run.update(status=report["status"], latest_note=event.did, updated_at=event.at)
            run.update(last_report_at=event.at, last_reported_by=data.get("reporter"))
            if report.get("execution_state") == "started" and run["execution_state"] == "prepared":
                run.update(execution_state="started", started_at=event.at)
            if report.get("progress") is not None:
                run["progress"] = report["progress"]
            if report.get("progress_report") is not None:
                run["progress_report"] = report["progress_report"]
            if report.get("progress") is not None or report.get("progress_report") is not None:
                run["progress_reported_at"] = event.at
            waiting = report.get("waiting")
            if waiting:
                same = previous_wait and all(previous_wait.get(k) == v for k, v in waiting.items())
                run["waiting"] = {**waiting, "since": previous_wait["since"] if same else event.at}
            else:
                run["waiting"] = None
            run["refs"] = list(dict.fromkeys([*run["refs"], *report.get("refs", [])]))
            if report.get("attempt_id"):
                run["attempt_id"] = report["attempt_id"]
            if report["status"] in TERMINAL_RUNS:
                run["ended_at"] = event.at
        elif event.event_type == "run_execution_authorized" and data.get("run_id") in runs:
            run = runs[data["run_id"]]
            run["execution_authorized_at"] = event.at
            run["request_key_hash"] = data.get("request_key_hash")
    for run in runs.values():
        run["lease_current"] = run["lease_term"] == int(task.lease_term or 0)
        run["lease_live"] = lease_is_live(task)
        # This is the card lease's heartbeat, not proof that this execution advanced.
        run["heartbeat_at"] = (_iso(task.lease_heartbeat_at) if run["lease_current"] else None)
        run["freshness"] = progress_freshness(run.get("progress_reported_at"))
    return list(runs.values())


def _iso(value: dt.datetime | None) -> str | None:
    return value.replace(tzinfo=value.tzinfo or dt.timezone.utc).isoformat() if value else None


def progress_freshness(reported_at: str | None) -> dict[str, Any]:
    age = None
    if reported_at:
        try:
            when = dt.datetime.fromisoformat(reported_at.replace("Z", "+00:00"))
            when = when.replace(tzinfo=when.tzinfo or dt.timezone.utc)
            age = max(0, int((utcnow() - when).total_seconds()))
        except (ValueError, TypeError):
            pass
    return {"state": "unknown" if age is None else ("stale" if age > PROGRESS_STALE_AFTER_SECONDS else "fresh"),
            "age_seconds": age, "stale_after_seconds": PROGRESS_STALE_AFTER_SECONDS}


def _registry_value(value: str | None, *, model: bool = False) -> str | None:
    cleaned = (value or "").strip()
    if not cleaned or (model and (model_identity_state(cleaned) == "unknown" or cleaned.lower() in {"auto", "default"})):
        return None
    try:
        return validate_ledger_text(cleaned, "identity snapshot", max_length=80)
    except ProtocolError:
        # Legacy operator-entered profiles may predate ledger text validation.
        return None


def _run_identity(db: Session, task: Task, principal: Principal, body: RunCreateBody,
                  now: str) -> dict[str, Any]:
    actor = _require_actor(db, task.holder, "run actor")
    node, runtime = _registry_value(actor.node), _registry_value(actor.runtime)
    model = _registry_value(body.model, model=True) or _registry_value(actor.model, model=True)
    if body.runtime_session_id is not None:
        session = db.get(RuntimeSession, body.runtime_session_id)
        # A relation is an explicit, task-owned reference, not session discovery.
        # Return one generic refusal even when the foreign row does not exist.
        if (session is None or session.actor_id != task.holder
                or (not principal.privileged and session.actor_id != principal.actor_id)
                or session.task_id != task.id or not node or not runtime
                or session.node != node or canonical_runtime(session.runtime) != canonical_runtime(runtime)):
            raise Forbidden("runtime session must belong to this actor, task, node and runtime")
    return {"node": node, "runtime": runtime, "model": model,
            "node_source": "registry" if node else "unknown",
            "runtime_source": "registry" if runtime else "unknown",
            "model_source": ("reported" if _registry_value(body.model, model=True) else "registry") if model else "unknown",
            "identity_recorded_at": now,
            "identity_complete": bool(node and runtime and model and model_identity_state(model) == "registered"),
            "runtime_session_id": body.runtime_session_id}


def _find_run(task: Task, run_id: str) -> dict[str, Any]:
    for run in runs_of(task):
        if run["id"] == run_id:
            return run
    raise ProtocolError("run not found on this task")


def start_run(db: Session, task: Task, principal: Principal, body: RunCreateBody) -> dict[str, Any]:
    _serialize_write(db, task)
    _assert_writer(task, principal, body.lease_term)
    _require_execution_actor(db, task.holder, "run actor")
    exclude = ({"runtime_session_id"} if body.runtime_session_id is None else set())
    if body.execution_state == "started":
        exclude.add("execution_state")
    request = body.model_dump(mode="json", exclude=exclude)
    key = _key("run", task.id, principal.write_identity, body.idempotency_key)
    existing = _existing(db, key, request)
    if existing is not None:
        return {"created": False, "run": _find_run(task, key)}
    if task.status != "doing":
        raise ProtocolError("start the held task before reporting an execution run")
    if any(r["status"] not in TERMINAL_RUNS and r["lease_current"] for r in runs_of(task)):
        raise Conflict("one active run per task lease; delegate parallel work to separate cards")
    now = utcnow().strftime("%Y-%m-%dT%H:%M:%S.%fZ")
    run = {
        "id": key, "task_id": task.id, "actor_id": task.holder,
        **_run_identity(db, task, principal, body, now),
        "session_ref": body.session_ref, "title": body.title, "status": "running",
        "module": body.module or ancestry(task).get("module"),
        "reported_by": {"kind": principal.kind, "id": principal.name},
        "started_at": now if body.execution_state == "started" else None,
        "prepared_at": now if body.execution_state == "prepared" else None,
        "execution_state": body.execution_state,
        "ended_at": None, "updated_at": now, "progress": None,
        "waiting": None, "latest_note": body.title, "refs": [], "attempt_id": None,
        "progress_report": None, "progress_reported_at": None,
        "lease_term": int(task.lease_term or 0),
    }
    _event(db, task, principal, key=key, kind="run_started", note=body.title,
           data={"run": run}, request=request)
    return {"created": True, "run": _find_run(task, key)}


def authorize_run_execution(db: Session, task_id: str, run_id: str, principal: Principal,
                            lease_term: int, request_key: str) -> dict[str, Any]:
    """Consume one launch permission. Even an exact retry never grants it again.

    This is an adapter gate, not a process lock or proof that execution occurred.
    The transaction must commit before the caller launches its configured runtime.
    """
    from .collaboration_schemas import ExecutionAuthorizationBody

    body = ExecutionAuthorizationBody(lease_term=lease_term, request_key=request_key)
    task = db.get(Task, task_id)
    if task is None:
        raise ProtocolError("task not found")
    _serialize_write(db, task)
    _assert_writer(task, principal, body.lease_term)
    _require_execution_actor(db, task.holder, "execution actor")
    if principal.write_identity != task.holder:
        raise Forbidden("only the current holder can request execution permission")
    run = _find_run(task, run_id)
    if (not lease_is_live(task) or not run["lease_current"] or run["actor_id"] != task.holder
            or run["status"] in TERMINAL_RUNS or task.status != "doing"):
        raise Conflict("execution permission requires an active run and a live current holder lease")
    for event in task.events:
        data = payload_of(event)
        if event.event_type == "run_execution_authorized" and data.get("run_id") == run_id:
            return {"allow_execute": False, "run_id": run_id, "execution_authorized_at": event.at,
                    "request_key_hash": data["request_key_hash"]}
    if run.get("execution_state") != "prepared":
        raise Conflict("execution permission is only for a prepared run; an already started run cannot launch again")
    digest = hashlib.sha256(body.request_key.encode()).hexdigest()
    key = _key("execution", task.id, run["actor_id"], run_id)
    data = {"run_id": run_id, "request_key_hash": digest}
    credential_id = getattr(principal, "credential_id", None)
    if credential_id is not None:
        data["credential_id"] = credential_id
    event = _event(db, task, principal, key=key, kind="run_execution_authorized",
                   note="Consumed one adapter execution permission",
                   data=data, request={"run_id": run_id, "lease_term": body.lease_term,
                                       "request_key_hash": digest})
    return {"allow_execute": True, "run_id": run_id, "execution_authorized_at": event.at,
            "request_key_hash": digest}


def _assert_report_writer(db: Session, task: Task, principal: Principal,
                          run_id: str, body: RunEventBody) -> None:
    try:
        _assert_writer(task, principal, body.lease_term)
    except Conflict:
        # A recorded semantic failure can expire the lease before the executor
        # closes its run. Permit only the terminal receipt of that already-stored
        # same-actor outcome. Never remint, reopen, move a baton or accept an old
        # term; a previous holder still cannot write this chain.
        current_term = int(task.lease_term or 0)
        if (body.status not in TERMINAL_RUNS or not body.attempt_id
                or principal.write_identity != task.holder
                or (body.lease_term is not None and body.lease_term != current_term)):
            raise
        run = _find_run(task, run_id)
        evidence = db.scalar(select(TaskAttempt).where(
            TaskAttempt.task_id == task.id, TaskAttempt.attempt_key == body.attempt_id))
        if (run["actor_id"] != task.holder or run["lease_term"] != current_term
                or evidence is None or evidence.lease_term != current_term
                or evidence.reporter_kind != "actor" or evidence.reporter_id != task.holder
                or evidence.outcome != body.status):
            raise


def report_run(db: Session, task: Task, principal: Principal,
               run_id: str, body: RunEventBody) -> dict[str, Any]:
    _serialize_write(db, task)
    _assert_report_writer(db, task, principal, run_id, body)
    exclude = {field for field in ("progress_report", "execution_state") if getattr(body, field) is None}
    request = {"run_id": run_id, **body.model_dump(mode="json", exclude=exclude)}
    key = _key("run-event", task.id, principal.write_identity, body.idempotency_key)
    existing = _existing(db, key, request)
    if existing is not None:
        return {"created": False, "run": _find_run(task, run_id)}
    run = _find_run(task, run_id)
    if run["actor_id"] != task.holder or not run["lease_current"]:
        raise Conflict("run belongs to a previous holder or lease; open a new run")
    if body.status not in RUN_TRANSITIONS.get(run["status"], set()):
        raise ProtocolError(f"illegal run transition: {run['status']} -> {body.status}")
    if body.execution_state == "started" and run.get("execution_state") == "prepared":
        _require_execution_actor(db, task.holder, "execution actor")
        if (not run.get("execution_authorized_at") or not lease_is_live(task)
                or principal.write_identity != task.holder):
            raise Conflict("prepared execution needs its consumed permission and a live holder lease before reporting started")
    if body.waiting:
        _require_actor(db, body.waiting.owner, "waiting owner")
    if body.progress_report and body.progress_report.next_owner:
        _require_actor(db, body.progress_report.next_owner, "next owner")
    if body.status in TERMINAL_RUNS and not body.attempt_id:
        raise ProtocolError("terminal run reports require the completed attempt id")
    if body.attempt_id:
        attempt = db.scalar(select(TaskAttempt).where(
            TaskAttempt.task_id == task.id, TaskAttempt.attempt_key == body.attempt_id))
        if attempt is None:
            raise ProtocolError("attempt must belong to this task")
        if body.status != attempt.outcome:
            raise ProtocolError("terminal run status must match the linked attempt outcome")
        if attempt.lease_term != run["lease_term"]:
            raise Conflict("attempt belongs to a different lease")
        if attempt.reporter_kind == "actor" and attempt.reporter_id != run["actor_id"]:
            raise Forbidden("attempt belongs to a different actor")
        if any(r["id"] != run_id and r.get("attempt_id") == body.attempt_id for r in runs_of(task)):
            raise Conflict("attempt is already linked to another run")
    report = body.model_dump(mode="json", exclude={"idempotency_key", "lease_term"})
    _event(db, task, principal, key=key, kind="run_reported", note=body.note,
           data={"run_id": run_id, "report": report}, request=request)
    return {"created": True, "run": _find_run(task, run_id)}


def task_tree(db: Session, task: Task) -> tuple[str, list[Task], list[dict[str, Any]]]:
    root_id = ancestry(task)["root_task_id"]
    root = db.get(Task, root_id)
    if root is None:
        raise ProtocolError("collaboration root is missing")
    tasks, delegations, visited, pending = [], [], set(), [root]
    while pending:
        current = pending.pop(0)
        if current.id in visited:
            continue
        visited.add(current.id)
        tasks.append(current)
        for event in current.events:
            data = payload_of(event).get("delegation")
            if event.event_type != "delegation" or not isinstance(data, dict):
                continue
            child = db.get(Task, data.get("child_task_id"))
            if child is None:
                continue
            relation = ancestry(child)
            # Both sides must attest the same relation. Arbitrary task text and
            # dependency links are never interpreted as collaboration edges.
            if (relation.get("parent_task_id") == current.id
                    and relation.get("delegation_id") == data.get("id")
                    and relation.get("root_task_id") == root_id):
                delegations.append(data)
                pending.append(child)
    return root_id, tasks, delegations


def event_dict(event: TaskEvent) -> dict[str, Any]:
    payload = payload_of(event)
    # The request is retained for exact idempotency, but not duplicated in UI feeds.
    payload.pop("request", None)
    return {"id": event.id, "task_id": event.task_id, "seq": event.seq,
            "type": event.event_type, "who": event.who, "did": event.did,
            "at": event.at, "payload": payload,
            "from_status": event.from_status, "to_status": event.to_status,
            "from_holder": event.from_holder, "to_holder": event.to_holder}


def collaboration_relationships(tasks: list[Task], graph: dict[str, Any]) -> list[dict[str, Any]]:
    """Read recorded dependency and baton facts; never parse instructions/notes."""
    relationships, seen_dependencies = [], set()
    for task in tasks:
        typed_transitions = {value["transition_event_id"]: value for event in task.events
                             if event.event_type == "pipeline_transition"
                             and isinstance((value := payload_of(event).get("pipeline_transition")), dict)
                             and isinstance(value.get("transition_event_id"), int)}
        for direction, items in graph[task.id].items():
            for item in items:
                prerequisite, dependent = ((item["id"], task.id) if direction == "blocked_by"
                                           else (task.id, item["id"]))
                edge_id = f"dependency:{prerequisite}:{dependent}"
                if edge_id not in seen_dependencies:
                    seen_dependencies.add(edge_id)
                    relationships.append({"id": edge_id, "kind": "dependency",
                                          "from_task_id": prerequisite, "to_task_id": dependent,
                                          "source": "task_dependency"})
        for event in task.events:
            if not event.from_holder or not event.to_holder or event.from_holder == event.to_holder:
                continue
            transition = typed_transitions.get(event.id) or {}
            kind = "review_return" if transition.get("kind") == "review_return" else "handoff"
            relationships.append({"id": f"{kind}:{event.id}", "kind": kind,
                                  "from_task_id": task.id, "to_task_id": task.id,
                                  "from_actor": event.from_holder, "to_actor": event.to_holder,
                                  "at": event.at, "note": event.did, "event_id": event.id,
                                  "source": "pipeline_transition" if transition else "task_event"})
    return relationships


def module_contributions(tasks: list[Task], runs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Group explicit product-module evidence without guessing from a title/dept."""
    modules: dict[str, dict[str, Any]] = {}

    def group(name: str | None) -> dict[str, Any]:
        key = name or ""
        if key not in modules:
            modules[key] = {"id": "module-" + hashlib.sha256(key.encode()).hexdigest()[:16],
                            "name": name or "未标注模块", "source": "explicit" if name else "unassigned",
                            "task_ids": [], "run_ids": [], "completed": []}
        return modules[key]

    task_modules = {task.id: ancestry(task).get("module") for task in tasks}
    instrumented = {run["task_id"] for run in runs}
    for task in tasks:
        # A run can provide explicit attribution when its task has no label.
        # Keep unknown history, but do not invent an empty unknown contribution
        # alongside that run merely because the task creation predates modules.
        if task_modules[task.id] or task.id not in instrumented:
            group(task_modules[task.id])["task_ids"].append(task.id)
    for run in runs:
        module = run.get("module") or task_modules.get(run["task_id"])
        item = group(module)
        if run["task_id"] not in item["task_ids"]:
            item["task_ids"].append(run["task_id"])
        item["run_ids"].append(run["id"])
        for completed in (run.get("progress_report") or {}).get("completed", []):
            target = group(completed.get("module") or module)
            if run["task_id"] not in target["task_ids"]:
                target["task_ids"].append(run["task_id"])
            if run["id"] not in target["run_ids"]:
                target["run_ids"].append(run["id"])
            target["completed"].append({"task_id": run["task_id"], "run_id": run["id"],
                                        "actor_id": run["actor_id"], "summary": completed["summary"],
                                        "refs": completed.get("refs", []), "revision": completed.get("revision"),
                                        "at": run.get("progress_reported_at"), "verification": "unverified"})
    return list(modules.values())


def recovery_for(db: Session, task: Task, principal: Principal) -> dict[str, Any]:
    available, reason = True, "恢复任务和租约后，等待执行器重新接续；不会自动启动进程。"
    if principal.role == "viewer" or principal.kind == "channel" or (
            not principal.privileged and task.holder != principal.write_identity):
        available, reason = False, "只有此分支的当前持棒者或操作员可以准备重试。"
    elif task.status != "blocked":
        available, reason = False, "仅为受阻分支准备重试；完成的分支保持不变。"
    elif task.pipeline_json:
        try:
            stage = json.loads(task.pipeline_json)[task.pipeline_stage]
        except (ValueError, IndexError, TypeError):
            stage = {"gate": "queen"}
        if stage.get("gate") == "queen":
            available, reason = False, "人工审批节点须通过审批流程继续。"
    if available:
        blockers = dependency_graph(db, [task.id])[task.id]["blocked_by"]
        if any(item["status"] != "done" for item in blockers):
            available, reason = False, "先完成此分支的前置任务。"
    return {"task_id": task.id, "available": available, "reason": reason,
            "action": "prepare_retry", "endpoint": f"/api/tasks/{task.id}/collaboration/retry" if available else None,
            "plan": retry_plan(task, task.attempts[-1] if task.attempts else None)}


def prepare_retry(db: Session, task: Task, principal: Principal, note: str) -> dict[str, Any]:
    _serialize_write(db, task)
    capability = recovery_for(db, task, principal)
    if not capability["available"]:
        if not principal.privileged and task.holder != principal.write_identity:
            raise Forbidden(capability["reason"])
        raise ProtocolError(capability["reason"])
    plan = capability["plan"]  # Preserve the pre-retry failure/session policy.
    retry_task(db, task, who=principal.write_identity, note=note,
               is_privileged=principal.privileged)
    return {"task": task_summary_to_dict(task), "retry_plan": plan,
            "execution_started": False}


def _read_watermarks(db: Session, task: Task) -> dict[str, int]:
    # Capture the ceiling before refreshing all loaded task relationships. A
    # child created between tree discovery and the feed query must not let its
    # parent event advance the cursor past a child event we did not discover.
    ceiling = {"event_id": db.scalar(select(func.max(TaskEvent.id))) or 0,
               "attempt_id": db.scalar(select(func.max(TaskAttempt.id))) or 0}
    db.expire_all()
    db.refresh(task)
    return ceiling


def collaboration_projection(db: Session, task: Task, principal: Principal) -> dict[str, Any]:
    if principal.kind == "channel":
        raise Forbidden("channel credentials cannot read shared collaboration context")
    ceiling = _read_watermarks(db, task)
    root_id, tasks, delegations = task_tree(db, task)
    graph = dependency_graph(db, [item.id for item in tasks])
    events = sorted((e for item in tasks for e in item.events), key=lambda e: e.id)
    attempts = sorted((a for item in tasks for a in item.attempts), key=lambda a: a.id)
    runs = [run for item in tasks for run in runs_of(item)]
    task_ids = {item.id for item in tasks}
    related_ids = {edge["id"] for value in graph.values() for items in value.values() for edge in items} - task_ids
    related_tasks = list(db.scalars(select(Task).where(Task.id.in_(related_ids)).order_by(Task.id))) if related_ids else []
    return {
        "task_id": task.id, "root_task_id": root_id,
        **delegation_capability(db, task, principal),
        "generated_at": utcnow().isoformat(),
        "cursor": ceiling,
        "tasks": [{**task_summary_to_dict(item, graph[item.id]),
                   "parent_task_id": ancestry(item).get("parent_task_id"),
                   "module": ancestry(item).get("module")} for item in tasks],
        "related_tasks": [task_summary_to_dict(item) for item in related_tasks],
        "relationships": collaboration_relationships(tasks, graph),
        "modules": module_contributions(tasks, runs),
        "delegations": delegations, "runs": runs,
        "events": [event_dict(event) for event in events],
        "attempts": [{"task_id": a.task_id, **attempt_to_dict(a)} for a in attempts],
        "recovery": [recovery_for(db, item, principal) for item in tasks],
        "coverage": {"instrumented_tasks": len({r["task_id"] for r in runs}), "total_tasks": len(tasks)},
    }


def collaboration_events(db: Session, task: Task, *, after_event_id: int = 0,
                         after_attempt_id: int = 0, limit: int = 100) -> dict[str, Any]:
    ceiling = _read_watermarks(db, task)
    root_id, tasks, _ = task_tree(db, task)
    ids = [item.id for item in tasks]
    events = list(db.scalars(select(TaskEvent).where(
        TaskEvent.task_id.in_(ids), TaskEvent.id > after_event_id,
        TaskEvent.id <= ceiling["event_id"],
    ).order_by(TaskEvent.id).limit(limit + 1)))
    attempts = list(db.scalars(select(TaskAttempt).where(
        TaskAttempt.task_id.in_(ids), TaskAttempt.id > after_attempt_id,
        TaskAttempt.id <= ceiling["attempt_id"],
    ).order_by(TaskAttempt.id).limit(limit + 1)))
    more = len(events) > limit or len(attempts) > limit
    events, attempts = events[:limit], attempts[:limit]
    return {"root_task_id": root_id,
            "events": [event_dict(event) for event in events],
            "attempts": [{"task_id": a.task_id, **attempt_to_dict(a)} for a in attempts],
            "cursor": {"event_id": events[-1].id if events else after_event_id,
                       "attempt_id": attempts[-1].id if attempts else after_attempt_id},
            "has_more": more}
