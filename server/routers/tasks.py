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
from ..engine import (
    UNSET,
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
    reclaim_expired_leases,
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
from ..helpers import (
    actor_name,
    get_task_or_404,
    notify_feishu,
    post_flow_cards,
    task_response,
)
from ..skill_ops import actor_skill_briefing
def proposal_for_task(_task):
    return None

def apply_roster_proposal(_db, _task, authorised_by):
    raise ProtocolError('roster import is not in this edition')
from ..schemas import (
    AttemptBody,
    ClaimBody,
    DispatchBody,
    EscalateBody,
    PrecheckBody,
    RetryBody,
    ReviewCommentBody,
    ReviewReplyBody,
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
            graph = dependency_graph(db, [task.id for task in page.items])
            return {
                "items": [
                    task_summary_to_dict(task, graph[task.id]) for task in page.items
                ],
                "next_cursor": page.next_cursor,
                "has_more": page.has_more,
            }
        tasks = list_tasks(
            db, status=status, holder=holder, include_archived=include_archived
        )
    except ProtocolError as exc:
        raise wrap_protocol_errors(exc) from exc
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
    reclaim_expired_leases(db)
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
    """Sweep expired leases and unclaimed hall cards. Any authenticated caller."""
    del principal
    results = reclaim_expired_leases(db)
    return {"reclaimed": results, "count": len(results)}


@router.post("/api/tasks")
def post_task(
    body: TaskCreateBody,
    principal: Principal = Depends(require_auth),
    db: Session = Depends(get_db, scope="function"),
) -> dict[str, Any]:
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
        )
    except ProtocolError as exc:
        raise wrap_protocol_errors(exc) from exc
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


@router.get("/api/tasks/{task_id}")
def get_task(
    task_id: str,
    principal: Principal = Depends(require_auth),
    db: Session = Depends(get_db, scope="function"),
) -> dict[str, Any]:
    return _task_projection(db, get_task_or_404(db, task_id))


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
    except ProtocolError as exc:
        raise wrap_protocol_errors(exc) from exc
    result = task_response(task)
    notify_feishu(result["receipt"])
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
