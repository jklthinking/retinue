"""Task board routes: tasks, dispatch, artifacts, reviews, and pipeline flow."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import FileResponse
from sqlalchemy.exc import IntegrityError, OperationalError
from sqlalchemy.orm import Session

from core.protocol.task import ProtocolError

from .. import feishu_cards
from ..db import Actor
from ..deps import Principal, get_db, require_admin, require_auth, wrap_protocol_errors
from ..dispatch import dispatch_intent, replay_after_conflict
from ..dispatch_v2 import (
    add_squad_member,
    apply_mentions,
    assign_task_squad,
    create_dispatch_schedule,
    create_squad,
    ingest_dispatch_event,
    route_squad_task,
    run_dispatch_sweeps,
    schedule_to_dict,
    squad_to_dict,
)
from ..engine import (
    UNSET,
    Forbidden,
    apply_reported_failure,
    audit_stored_task,
    append_attempt,
    append_review_comment,
    append_review_reply,
    attempt_to_dict,
    build_start_briefing,
    claim_task,
    create_task,
    dependency_graph,
    add_task_dependency,
    escalate_task,
    heartbeat_task,
    precheck_deliverable,
    remove_task_dependency,
    retry_plan,
    retry_task,
    list_ready_tasks,
    list_task_summaries,
    list_tasks,
    reviews_from_task,
    task_summary_to_dict,
    task_to_dict,
    update_task,
)
from ..flow import pipeline_of, stage_done, stage_reject, validate_pipeline
from ..guardrails import assert_done_allowed
from ..pipeline_v2 import record_guardrail_event
from ..helpers import (
    actor_name,
    get_task_or_404,
    notify_feishu,
    post_flow_cards,
    task_response,
)
from ..intake import open_channel_card
from ..skill_ops import actor_skill_briefing
def proposal_for_task(_task):
    return None

def apply_roster_proposal(_db, _task, authorised_by):
    raise ProtocolError('roster import is not in this edition')
from ..schemas import (
    AttemptBody,
    ClaimBody,
    DispatchBody,
    DispatchEventBody,
    DispatchScheduleBody,
    EscalateBody,
    PrecheckBody,
    RetryBody,
    ReviewCommentBody,
    ReviewReplyBody,
    SquadBody,
    SquadMemberBody,
    SquadRouteBody,
    TaskSquadBody,
    StageDoneBody,
    StageRejectBody,
    TaskCreateBody,
    TaskDependencyBody,
    TaskDependencyRemoveBody,
    TaskHeartbeatBody,
    TaskUpdateBody,
)

router = APIRouter()


def _task_projection(db: Session, task) -> dict[str, Any]:
    relations = dependency_graph(db, [task.id])[task.id]
    return task_to_dict(task, relations)


def _channel_may_read(principal: Principal, task) -> bool:
    """A channel credential sees only the cards its own channel opened."""
    if principal.kind != "channel":
        return True
    return task.source_channel == principal.name


@router.get("/api/tasks")
def get_tasks(
    status: str | None = None,
    holder: str | None = None,
    include_archived: bool = False,
    page_size: int | None = None,
    cursor: str | None = None,
    principal: Principal = Depends(require_auth),
    db: Session = Depends(get_db, scope="function"),
) -> list[dict[str, Any]] | dict[str, Any]:
    try:
        if page_size is not None or cursor is not None:
            page = list_task_summaries(
                db,
                page_size=page_size if page_size is not None else 50,
                cursor=cursor,
                status=status,
                holder=holder,
                include_archived=include_archived,
            )
            items = [
                task
                for task in page.items
                if _channel_may_read(principal, task)
            ]
            graph = dependency_graph(db, [task.id for task in items])
            return {
                "items": [
                    task_summary_to_dict(task, graph[task.id]) for task in items
                ],
                "next_cursor": page.next_cursor,
                "has_more": page.has_more,
            }
        tasks = list_tasks(
            db, status=status, holder=holder, include_archived=include_archived
        )
    except ProtocolError as exc:
        raise wrap_protocol_errors(exc) from exc
    tasks = [task for task in tasks if _channel_may_read(principal, task)]
    graph = dependency_graph(db, [task.id for task in tasks])
    return [task_to_dict(task, graph[task.id]) for task in tasks]


@router.get("/api/tasks/ready")
def get_ready_tasks(
    holder: str | None = None,
    include_archived: bool = False,
    principal: Principal = Depends(require_auth),
    db: Session = Depends(get_db, scope="function"),
) -> list[dict[str, Any]]:
    """Return queued cards whose finish-to-start prerequisites are all done."""
    from ..reminders import bound_data_dir, deliver_due_reminders

    run_dispatch_sweeps(db)
    deliver_due_reminders(db, data_dir=bound_data_dir())
    tasks = list_ready_tasks(
        db, holder=holder, include_archived=include_archived
    )
    graph = dependency_graph(db, [task.id for task in tasks])
    return [task_summary_to_dict(task, graph[task.id]) for task in tasks]


@router.post("/api/tasks/reclaim")
def post_task_reclaim(
    principal: Principal = Depends(require_auth),
    db: Session = Depends(get_db, scope="function"),
) -> dict[str, Any]:
    """Sweep expired leases, due schedules, squad routes, and due reminders."""
    from ..reminders import bound_data_dir, deliver_due_reminders

    del principal
    result = run_dispatch_sweeps(db)
    result["reminders"] = deliver_due_reminders(db, data_dir=bound_data_dir())
    return result


@router.post("/api/tasks")
def post_task(
    body: TaskCreateBody,
    principal: Principal = Depends(require_auth),
    db: Session = Depends(get_db, scope="function"),
) -> dict[str, Any]:
    if principal.kind == "channel":
        return _post_channel_task(body, principal, db)
    if body.source_user is not None:
        raise HTTPException(
            status_code=422,
            detail="source_user 是通道令牌专属的来源字段,用户与执行者不可伪造",
        )
    creator = principal.write_identity
    if db.get(Actor, creator) is None:
        raise HTTPException(
            status_code=422,
            detail=f"当前账号未绑定执行者(actor):{creator!r};请管理员先建立并绑定",
        )
    stages = None
    if body.pipeline is not None:
        if body.open_dispatch:
            raise HTTPException(
                status_code=422, detail="流程任务不能同时挂单;首节点执行者即接棒人"
            )
        try:
            stages = validate_pipeline(db, [s.model_dump() for s in body.pipeline])
        except ProtocolError as exc:
            raise wrap_protocol_errors(exc) from exc
        holder = stages[0]["holder"]
    else:
        holder = body.holder or creator
        if body.holder is None and not body.open_dispatch:
            raise HTTPException(
                status_code=422, detail="holder is required unless open_dispatch is set"
            )
        if body.open_dispatch and body.holder and body.holder != creator:
            raise HTTPException(
                status_code=422, detail="挂单任务不能同时指派执行者;二者只能选其一"
            )
    # An agent credential is one actor's identity, not dispatch authority over
    # the roster. Free-form creation may hand work to the agent itself or to
    # the open hall; naming another executor is what member and admin accounts
    # are for. Template dispatch (/api/dispatch) stays open to agents because
    # its stage holders come from admin-curated templates, not from the caller.
    if principal.kind == "agent":
        assigned = (
            {stage["holder"] for stage in stages}
            if stages is not None
            else {holder}
        )
        strangers = sorted(assigned - {creator})
        if strangers:
            raise HTTPException(
                status_code=403,
                detail=(
                    "agent 令牌不能把新卡派给其他执行者:"
                    f"{', '.join(strangers)};请改用挂单,或由成员账号派卡"
                ),
            )
    try:
        task = create_task(
            db,
            title=body.title,
            created_by=creator,
            holder=holder,
            dept=body.dept,
            priority=body.priority,
            acceptance=body.acceptance,
            depends_on=body.depends_on,
            due_at=body.due_at,
            note=body.note,
            open_dispatch=body.open_dispatch,
            squad_id=body.squad_id,
        )
    except ProtocolError as exc:
        raise wrap_protocol_errors(exc) from exc
    if body.note:
        apply_mentions(
            db,
            task,
            who=creator,
            text=body.note,
            source_type="create",
            source_key=f"create:{task.id}",
            is_privileged=principal.privileged,
        )
    if stages is not None:
        task.pipeline_json = json.dumps(stages, ensure_ascii=False)
        task.pipeline_stage = 0
        db.flush()
        feishu_cards.send_baton_card(
            task_to_dict(task), stages[0]["name"], actor_name(db, stages[0]["holder"])
        )
    result = task_response(task)
    result.update(task_summary_to_dict(task, dependency_graph(db, [task.id])[task.id]))
    notify_feishu(result["receipt"])
    return result


def _post_channel_task(
    body: TaskCreateBody, principal: Principal, db: Session
) -> dict[str, Any]:
    """Open a hall card for a channel credential (intake protocol M0).

    The publish specification: always open dispatch, signed by the mapped
    board user of the attested ``source_user``, never naming an executor —
    a channel token is an intake identity, not dispatch authority.
    """
    if body.pipeline is not None:
        raise HTTPException(status_code=422, detail="通道令牌不能开流程卡")
    if body.squad_id:
        raise HTTPException(status_code=422, detail="通道令牌不能指定小队")
    if not body.open_dispatch:
        raise HTTPException(
            status_code=422, detail="通道开卡必须挂单(open_dispatch)"
        )
    if body.holder is not None:
        raise HTTPException(
            status_code=403, detail="通道令牌不能指派执行者,只能挂单"
        )
    if body.depends_on:
        raise HTTPException(status_code=422, detail="通道开卡不支持依赖编排")
    if not body.source_user:
        raise HTTPException(
            status_code=422, detail="通道开卡必须带 source_user 来源标识"
        )
    try:
        task = open_channel_card(
            db,
            channel_id=principal.name,
            channel_user_id=body.source_user,
            title=body.title,
            note=body.note,
            acceptance=body.acceptance,
            dept=body.dept,
            priority=body.priority,
        )
    except Forbidden as exc:
        raise wrap_protocol_errors(exc) from exc
    result = task_response(task)
    result.update(task_summary_to_dict(task, dependency_graph(db, [task.id])[task.id]))
    notify_feishu(result["receipt"])
    return result


@router.post("/api/dispatch")
def post_dispatch(
    body: DispatchBody,
    principal: Principal = Depends(require_auth),
    db: Session = Depends(get_db, scope="function"),
) -> dict[str, Any]:
    """Turn one source event into one canonical pipeline card."""
    creator = principal.write_identity
    if db.get(Actor, creator) is None:
        raise HTTPException(
            status_code=422,
            detail=f"当前账号未绑定执行者(actor):{creator!r};请管理员先建立并绑定",
        )
    arguments = {
        "actor_id": creator,
        "intent": body.intent,
        "idempotency_key": body.idempotency_key,
        "template_name": body.template_name,
        "priority": body.priority,
        "acceptance": body.acceptance,
    }
    try:
        outcome = dispatch_intent(db, **arguments)
    except IntegrityError:
        db.rollback()
        try:
            outcome = replay_after_conflict(db, **arguments)
        except ProtocolError as exc:
            raise wrap_protocol_errors(exc) from exc
    except ProtocolError as exc:
        raise wrap_protocol_errors(exc) from exc

    task = outcome.task
    if outcome.created:
        stages = pipeline_of(task)
        feishu_cards.send_baton_card(
            task_to_dict(task),
            stages[0]["name"],
            actor_name(db, stages[0]["holder"]),
        )
    result = task_response(task)
    result.update(
        {
            "created": outcome.created,
            "idempotency_key": body.idempotency_key,
            "matched_template": {
                "id": outcome.template.id,
                "name": outcome.template.name,
            },
            "matched_terms": outcome.matched_terms,
        }
    )
    if outcome.created:
        notify_feishu(result["receipt"])
    return result


@router.get("/api/squads")
def get_squads(
    principal: Principal = Depends(require_auth),
    db: Session = Depends(get_db, scope="function"),
) -> list[dict[str, Any]]:
    del principal
    from sqlalchemy import select

    from ..db import Squad

    rows = list(db.execute(select(Squad).order_by(Squad.id)).scalars())
    return [squad_to_dict(db, row) for row in rows]


@router.post("/api/squads")
def post_squad(
    body: SquadBody,
    principal: Principal = Depends(require_admin),
    db: Session = Depends(get_db, scope="function"),
) -> dict[str, Any]:
    try:
        squad = create_squad(
            db,
            squad_id=body.id,
            display_name=body.display_name,
            leader_id=body.leader_id,
            created_by=principal.write_identity,
            members=body.members,
        )
    except ProtocolError as exc:
        raise wrap_protocol_errors(exc) from exc
    return squad_to_dict(db, squad)


@router.post("/api/squads/{squad_id}/members")
def post_squad_member(
    squad_id: str,
    body: SquadMemberBody,
    principal: Principal = Depends(require_admin),
    db: Session = Depends(get_db, scope="function"),
) -> dict[str, Any]:
    del principal
    from ..db import Squad

    squad = db.get(Squad, squad_id)
    if squad is None:
        raise HTTPException(status_code=404, detail=f"unknown squad: {squad_id}")
    try:
        add_squad_member(db, squad, actor_id=body.actor_id)
    except ProtocolError as exc:
        raise wrap_protocol_errors(exc) from exc
    return squad_to_dict(db, squad)


@router.get("/api/dispatch/schedules")
def get_dispatch_schedules(
    principal: Principal = Depends(require_auth),
    db: Session = Depends(get_db, scope="function"),
) -> list[dict[str, Any]]:
    del principal
    from sqlalchemy import select

    from ..db import DispatchSchedule

    rows = list(
        db.execute(select(DispatchSchedule).order_by(DispatchSchedule.id)).scalars()
    )
    return [schedule_to_dict(row) for row in rows]


@router.post("/api/dispatch/schedules")
def post_dispatch_schedule(
    body: DispatchScheduleBody,
    principal: Principal = Depends(require_auth),
    db: Session = Depends(get_db, scope="function"),
) -> dict[str, Any]:
    if not principal.privileged:
        raise HTTPException(status_code=403, detail="only members may create schedules")
    try:
        row = create_dispatch_schedule(
            db,
            schedule_key=body.schedule_key,
            title=body.title,
            fire_at=body.fire_at,
            created_by=principal.write_identity,
            holder=body.holder,
            open_dispatch=body.open_dispatch,
            squad_id=body.squad_id,
            dept=body.dept,
            priority=body.priority,
            acceptance=body.acceptance,
            note=body.note,
            repeat_seconds=body.repeat_seconds,
        )
    except ProtocolError as exc:
        raise wrap_protocol_errors(exc) from exc
    return schedule_to_dict(row)


@router.post("/api/dispatch/events")
def post_dispatch_event(
    body: DispatchEventBody,
    principal: Principal = Depends(require_auth),
    db: Session = Depends(get_db, scope="function"),
) -> dict[str, Any]:
    creator = principal.write_identity
    if db.get(Actor, creator) is None:
        raise HTTPException(
            status_code=422,
            detail=f"当前账号未绑定执行者(actor):{creator!r};请管理员先建立并绑定",
        )
    if principal.kind == "agent":
        assigned = body.holder or creator
        if body.holder and body.holder != creator and not body.open_dispatch:
            raise HTTPException(
                status_code=403,
                detail=(
                    "agent 令牌不能把新卡派给其他执行者:"
                    f"{body.holder};请改用挂单,或由成员账号派卡"
                ),
            )
        del assigned
    try:
        task, created = ingest_dispatch_event(
            db,
            actor_id=creator,
            source=body.source,
            idempotency_key=body.idempotency_key,
            title=body.title,
            holder=body.holder,
            open_dispatch=body.open_dispatch,
            squad_id=body.squad_id,
            dept=body.dept,
            priority=body.priority,
            acceptance=body.acceptance,
            note=body.note,
        )
    except ProtocolError as exc:
        raise wrap_protocol_errors(exc) from exc
    if created and (body.note or body.title):
        apply_mentions(
            db,
            task,
            who=creator,
            text=" ".join(part for part in (body.title, body.note) if part),
            source_type=body.source,
            source_key=f"{body.source}:{body.idempotency_key}",
            is_privileged=principal.privileged,
        )
    result = task_response(task)
    result.update(
        {
            "created": created,
            "source": body.source,
            "idempotency_key": body.idempotency_key,
        }
    )
    result.update(task_summary_to_dict(task, dependency_graph(db, [task.id])[task.id]))
    if created:
        notify_feishu(result["receipt"])
    return result


@router.get("/api/tasks/{task_id}")
def get_task(
    task_id: str,
    principal: Principal = Depends(require_auth),
    db: Session = Depends(get_db, scope="function"),
) -> dict[str, Any]:
    task = get_task_or_404(db, task_id)
    if not _channel_may_read(principal, task):
        raise HTTPException(
            status_code=403, detail="通道令牌只能读自己开的卡"
        )
    return _task_projection(db, task)


@router.get("/api/tasks/{task_id}/drift")
def get_task_drift(
    task_id: str,
    principal: Principal = Depends(require_auth),
    db: Session = Depends(get_db, scope="function"),
) -> dict[str, Any]:
    """Fold the immutable chain and compare it with the row, without writes."""
    return audit_stored_task(get_task_or_404(db, task_id))


@router.post("/api/tasks/{task_id}/dependencies")
def post_task_dependency(
    task_id: str,
    body: TaskDependencyBody,
    principal: Principal = Depends(require_auth),
    db: Session = Depends(get_db, scope="function"),
) -> dict[str, Any]:
    task = get_task_or_404(db, task_id)
    try:
        _edge, created = add_task_dependency(
            db,
            task,
            prerequisite_id=body.prerequisite_id,
            kind=body.kind,
            who=principal.write_identity,
            is_privileged=principal.privileged,
            note=body.note,
        )
    except ProtocolError as exc:
        raise wrap_protocol_errors(exc) from exc
    return {"created": created, "task": _task_projection(db, task)}


@router.delete("/api/tasks/{task_id}/dependencies/{prerequisite_id}")
def delete_task_dependency(
    task_id: str,
    prerequisite_id: str,
    body: TaskDependencyRemoveBody,
    principal: Principal = Depends(require_auth),
    db: Session = Depends(get_db, scope="function"),
) -> dict[str, Any]:
    task = get_task_or_404(db, task_id)
    try:
        remove_task_dependency(
            db,
            task,
            prerequisite_id=prerequisite_id,
            who=principal.write_identity,
            is_privileged=principal.privileged,
            note=body.note,
        )
    except ProtocolError as exc:
        raise wrap_protocol_errors(exc) from exc
    return {"removed": True, "task": _task_projection(db, task)}


@router.get("/api/tasks/{task_id}/attempts")
def get_task_attempts(
    task_id: str,
    principal: Principal = Depends(require_auth),
    db: Session = Depends(get_db, scope="function"),
) -> list[dict[str, Any]]:
    task = get_task_or_404(db, task_id)
    return [attempt_to_dict(attempt) for attempt in task.attempts]


@router.post("/api/tasks/{task_id}/attempts")
def post_task_attempt(
    task_id: str,
    body: AttemptBody,
    principal: Principal = Depends(require_auth),
    db: Session = Depends(get_db, scope="function"),
) -> dict[str, Any]:
    task = get_task_or_404(db, task_id)
    if principal.kind == "agent":
        reporter_kind = "actor"
        reporter_id = principal.actor_id or principal.name
        if task.holder != reporter_id:
            raise HTTPException(
                status_code=403,
                detail="only the current holder may report an actor attempt",
            )
    else:
        # A browser session remains operator-attributed even when its user is
        # bound to an actor. Only that actor's bearer token can claim the actor
        # identity in an attempt record.
        reporter_kind = "operator"
        reporter_id = principal.name
    try:
        attempt, created = append_attempt(
            db,
            task,
            reporter_kind=reporter_kind,
            reporter_id=reporter_id,
            duty=None,
            outcome=body.outcome,
            started_at=body.started_at,
            ended_at=body.ended_at,
            reason=body.reason,
            exit_status=body.exit_status,
            idempotency_key=body.idempotency_key,
            lease_term=body.lease_term,
            trigger_source=body.trigger_source,
            session_ref=body.session_ref,
            checkpoint_ref=body.checkpoint_ref,
            failure_class=body.failure_class,
            workdir_key=body.workdir_key,
            is_privileged=principal.privileged,
        )
    except ProtocolError as exc:
        raise wrap_protocol_errors(exc) from exc
    policy = None
    if created and body.outcome == "failed" and body.failure_class:
        try:
            policy = apply_reported_failure(
                db,
                task,
                who=principal.write_identity,
                failure_class=body.failure_class,
                reason=body.reason or "execution failed",
                lease_term=body.lease_term,
                is_privileged=principal.privileged,
            )
        except ProtocolError as exc:
            raise wrap_protocol_errors(exc) from exc
    return {
        "task_id": task.id,
        "task_status": task.status,
        "created": created,
        "attempt": attempt_to_dict(attempt),
        "policy": policy,
    }


@router.get("/api/artifacts/{task_id}")
def get_task_artifact(
    task_id: str,
    request: Request,
    principal: Principal = Depends(require_auth),
    db: Session = Depends(get_db, scope="function"),
) -> FileResponse:
    """Serve a tenant-local Markdown deliverable for an authenticated task."""
    task = get_task_or_404(db, task_id)
    data_dir = request.app.state.data_dir
    if data_dir is None:
        raise HTTPException(status_code=404, detail="artifact storage is not configured")
    artifact = Path(data_dir) / "artifacts" / f"{task.id}.md"
    if not artifact.is_file():
        raise HTTPException(status_code=404, detail=f"artifact not found: {task.id}")
    return FileResponse(
        artifact,
        media_type="text/markdown; charset=utf-8",
        content_disposition_type="inline",
    )


@router.get("/api/tasks/{task_id}/reviews")
def get_task_reviews(
    task_id: str,
    principal: Principal = Depends(require_auth),
    db: Session = Depends(get_db, scope="function"),
) -> list[dict[str, Any]]:
    return reviews_from_task(get_task_or_404(db, task_id))


@router.post("/api/tasks/{task_id}/reviews")
def post_task_review(
    task_id: str,
    body: ReviewCommentBody,
    principal: Principal = Depends(require_auth),
    db: Session = Depends(get_db, scope="function"),
) -> dict[str, Any]:
    task = get_task_or_404(db, task_id)
    try:
        event, created = append_review_comment(
            db,
            task,
            who=principal.write_identity,
            idempotency_key=body.idempotency_key,
            body=body.body,
            artifact_ref=body.artifact_ref,
        )
    except ProtocolError as exc:
        raise wrap_protocol_errors(exc) from exc
    if created:
        apply_mentions(
            db,
            task,
            who=principal.write_identity,
            text=body.body,
            source_type="review",
            source_key=event.event_key or f"review:{event.seq}",
            is_privileged=principal.privileged,
        )
    reviews = reviews_from_task(task)
    return {
        "task_id": task.id,
        "created": created,
        "chain_seq": event.seq,
        "review": next(item for item in reviews if item["id"] == event.event_key),
        "task_status": task.status,
    }


@router.post("/api/tasks/{task_id}/reviews/{review_id}/replies")
def post_task_review_reply(
    task_id: str,
    review_id: str,
    body: ReviewReplyBody,
    principal: Principal = Depends(require_auth),
    db: Session = Depends(get_db, scope="function"),
) -> dict[str, Any]:
    task = get_task_or_404(db, task_id)
    try:
        event, created = append_review_reply(
            db,
            task,
            who=principal.write_identity,
            review_id=review_id,
            idempotency_key=body.idempotency_key,
            body=body.body,
            decision=body.decision,
            evidence_refs=body.evidence_refs,
        )
    except ProtocolError as exc:
        raise wrap_protocol_errors(exc) from exc
    reviews = reviews_from_task(task)
    return {
        "task_id": task.id,
        "created": created,
        "chain_seq": event.seq,
        "review": next(item for item in reviews if item["id"] == review_id),
        "task_status": task.status,
    }


@router.post("/api/tasks/{task_id}/update")
def post_task_update(
    task_id: str,
    body: TaskUpdateBody,
    principal: Principal = Depends(require_auth),
    db: Session = Depends(get_db, scope="function"),
) -> dict[str, Any]:
    task = get_task_or_404(db, task_id)
    if (
        proposal_for_task(task) is not None
        and body.status is not None
        and body.status != "cancelled"
    ):
        raise HTTPException(
            status_code=422,
            detail="roster proposals change status only through the explicit approval action",
        )
    try:
        verdict = None
        if body.status == "done":
            verdict = assert_done_allowed(task.acceptance, body.evidence)
        task = update_task(
            db,
            task,
            who=principal.write_identity,
            is_privileged=principal.privileged,
            status=body.status,
            holder=body.holder,
            dept=body.dept,
            blocked_reason=body.blocked_reason,
            next_holder=body.next_holder,
            due_at=body.due_at if "due_at" in body.model_fields_set else UNSET,
            priority=body.priority,
            acceptance=body.acceptance,
            refs=body.refs,
            note=body.note,
            progress=body.progress,
            lease_term=body.lease_term,
        )
        if verdict is not None:
            record_guardrail_event(
                db, task, who=principal.write_identity, verdict=verdict
            )
    except ProtocolError as exc:
        raise wrap_protocol_errors(exc) from exc
    if body.note:
        apply_mentions(
            db,
            task,
            who=principal.write_identity,
            text=body.note,
            source_type="note",
            source_key=f"note:{task.events[-1].seq if task.events else 0}",
            is_privileged=principal.privileged,
        )
    result = task_response(task)
    notify_feishu(result["receipt"])
    return result


@router.post("/api/tasks/{task_id}/squad")
def post_task_squad(
    task_id: str,
    body: TaskSquadBody,
    principal: Principal = Depends(require_auth),
    db: Session = Depends(get_db, scope="function"),
) -> dict[str, Any]:
    if not principal.privileged:
        raise HTTPException(status_code=403, detail="only members may address a squad")
    task = get_task_or_404(db, task_id)
    try:
        assign_task_squad(
            db, task, squad_id=body.squad_id, who=principal.write_identity
        )
    except ProtocolError as exc:
        raise wrap_protocol_errors(exc) from exc
    return _task_projection(db, task)


@router.post("/api/tasks/{task_id}/squad-route")
def post_task_squad_route(
    task_id: str,
    body: SquadRouteBody | None = None,
    principal: Principal = Depends(require_auth),
    db: Session = Depends(get_db, scope="function"),
) -> dict[str, Any]:
    payload = body or SquadRouteBody()
    task = get_task_or_404(db, task_id)
    try:
        routed = route_squad_task(
            db,
            task,
            who=principal.write_identity,
            is_privileged=principal.privileged,
            member_id=payload.member_id,
        )
    except ProtocolError as exc:
        raise wrap_protocol_errors(exc) from exc
    result = _task_projection(db, task)
    result["routed"] = routed
    return result


@router.post("/api/tasks/{task_id}/apply-proposal")
def post_apply_roster_proposal(
    task_id: str,
    principal: Principal = Depends(require_admin),
    db: Session = Depends(get_db, scope="function"),
) -> dict[str, Any]:
    task = get_task_or_404(db, task_id)
    try:
        applied = apply_roster_proposal(
            db,
            task,
            authorised_by=principal.write_identity,
        )
    except ProtocolError as exc:
        raise wrap_protocol_errors(exc) from exc
    result = task_response(task)
    result["applied"] = applied
    notify_feishu(result["receipt"])
    return result


@router.post("/api/tasks/{task_id}/claim")
def post_task_claim(
    task_id: str,
    body: ClaimBody,
    principal: Principal = Depends(require_auth),
    db: Session = Depends(get_db, scope="function"),
) -> dict[str, Any]:
    task = get_task_or_404(db, task_id)
    claimant = principal.write_identity
    if db.get(Actor, claimant) is None:
        raise HTTPException(status_code=422, detail=f"当前账号未绑定执行者: {claimant!r}")
    try:
        task = claim_task(db, task, claimant=claimant, note=body.note)
    except ProtocolError as exc:
        raise wrap_protocol_errors(exc) from exc
    except (IntegrityError, OperationalError) as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail="该单刚被抢先接走,请刷新大厅") from exc
    result = task_response(task)
    briefing = build_start_briefing(db, task, claimant)
    try:
        skills = actor_skill_briefing(db, claimant)
    except ProtocolError:
        skills = {
            "actor_id": claimant,
            "display_name": claimant,
            "skills": [],
            "count": 0,
            "note": "No bound skills for this executor.",
        }
    result["skill_briefing"] = skills
    result["start_briefing"] = {**briefing, "related_skills": skills}
    notify_feishu(result["receipt"])
    return result


# ---------- pipeline flow ----------

@router.post("/api/tasks/{task_id}/stage-done")
def post_stage_done(
    task_id: str,
    body: StageDoneBody,
    principal: Principal = Depends(require_auth),
    db: Session = Depends(get_db, scope="function"),
) -> dict[str, Any]:
    task = get_task_or_404(db, task_id)
    try:
        if task.pipeline_json:
            stages = pipeline_of(task)
            if task.pipeline_stage + 1 >= len(stages):
                verdict = assert_done_allowed(task.acceptance, body.evidence)
                if verdict.get("checks"):
                    record_guardrail_event(
                        db, task, who=principal.write_identity, verdict=verdict
                    )
        outcome = stage_done(
            db,
            task,
            who=principal.write_identity,
            is_privileged=principal.privileged,
            note=body.note,
            confidence=body.confidence,
        )
    except ProtocolError as exc:
        raise wrap_protocol_errors(exc) from exc
    post_flow_cards(db, task, outcome)
    result = task_response(task)
    notify_feishu(result["receipt"])
    return result


@router.post("/api/tasks/{task_id}/stage-reject")
def post_stage_reject(
    task_id: str,
    body: StageRejectBody,
    principal: Principal = Depends(require_auth),
    db: Session = Depends(get_db, scope="function"),
) -> dict[str, Any]:
    task = get_task_or_404(db, task_id)
    try:
        outcome = stage_reject(
            db,
            task,
            who=principal.write_identity,
            is_privileged=principal.privileged,
            note=body.note,
        )
    except ProtocolError as exc:
        raise wrap_protocol_errors(exc) from exc
    post_flow_cards(db, task, outcome)
    result = task_response(task)
    notify_feishu(result["receipt"])
    return result


@router.post("/api/tasks/{task_id}/heartbeat")
def post_task_heartbeat(
    task_id: str,
    body: TaskHeartbeatBody,
    principal: Principal = Depends(require_auth),
    db: Session = Depends(get_db, scope="function"),
) -> dict[str, Any]:
    task = get_task_or_404(db, task_id)
    try:
        task = heartbeat_task(
            db,
            task,
            who=principal.write_identity,
            lease_term=body.lease_term,
            started=body.started,
            workdir_key=body.workdir_key,
        )
    except ProtocolError as exc:
        raise wrap_protocol_errors(exc) from exc
    return task_response(task)


@router.post("/api/tasks/{task_id}/precheck")
def post_task_precheck(
    task_id: str,
    body: PrecheckBody,
    principal: Principal = Depends(require_auth),
    db: Session = Depends(get_db, scope="function"),
) -> dict[str, Any]:
    task = get_task_or_404(db, task_id)
    try:
        verdict = precheck_deliverable(
            db,
            task,
            who=principal.write_identity,
            lease_term=body.lease_term,
            checks=[item.model_dump() for item in body.checks],
            is_privileged=principal.privileged,
        )
    except ProtocolError as exc:
        raise wrap_protocol_errors(exc) from exc
    result = task_response(task)
    result["precheck"] = verdict
    return result


@router.post("/api/tasks/{task_id}/escalate")
def post_task_escalate(
    task_id: str,
    body: EscalateBody,
    principal: Principal = Depends(require_auth),
    db: Session = Depends(get_db, scope="function"),
) -> dict[str, Any]:
    task = get_task_or_404(db, task_id)
    try:
        task = escalate_task(
            db,
            task,
            who=principal.write_identity,
            note=body.note,
            reason=body.reason,
            lease_term=body.lease_term,
            is_privileged=principal.privileged,
        )
    except ProtocolError as exc:
        raise wrap_protocol_errors(exc) from exc
    result = task_response(task)
    notify_feishu(result["receipt"])
    return result


@router.post("/api/tasks/{task_id}/retry")
def post_task_retry(
    task_id: str,
    body: RetryBody,
    principal: Principal = Depends(require_auth),
    db: Session = Depends(get_db, scope="function"),
) -> dict[str, Any]:
    task = get_task_or_404(db, task_id)
    try:
        task = retry_task(
            db,
            task,
            who=principal.write_identity,
            note=body.note,
            is_privileged=principal.privileged,
            workdir_key=body.workdir_key,
        )
    except ProtocolError as exc:
        raise wrap_protocol_errors(exc) from exc
    result = task_response(task)
    last = task.attempts[-1] if task.attempts else None
    result["retry_plan"] = retry_plan(task, last)
    notify_feishu(result["receipt"])
    return result
