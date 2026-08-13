"""Pipeline flows and queen-gate approvals on top of the task protocol.

A pipeline is an ordered list of stages, each `{name, holder, gate}` with
gate ∈ {auto, review, queen}:

- auto:   the stage holder works and hands the baton on with `stage_done`.
- review: same mechanics, but the holder may also `stage_reject` the baton
  back to the previous stage.
- queen:  a human decision node — entering it opens an Approval that the
  gate holder settles in the web console or via a signed Feishu card link.

Every movement is an ordinary protocol event (handoff/doing/done) so the
chain, holder-only-writes, and receipts keep working unchanged.
"""

from __future__ import annotations

import json
from typing import Any, Iterable

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from core.protocol.task import ID_RE, ProtocolError

from .db import Actor, Approval, Task, utcnow
from .engine import Conflict, update_task
from .security import hash_token, new_token

GATES = ("auto", "review", "queen")


def validate_pipeline(db: Session, stages: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    cleaned: list[dict[str, Any]] = []
    for index, stage in enumerate(stages):
        if not isinstance(stage, dict):
            raise ProtocolError(f"pipeline[{index}] must be a mapping")
        name = str(stage.get("name") or "").strip()
        holder = str(stage.get("holder") or "").strip()
        gate = str(stage.get("gate") or "auto").strip()
        if not name:
            raise ProtocolError(f"pipeline[{index}].name must be non-empty")
        if gate not in GATES:
            raise ProtocolError(f"pipeline[{index}].gate must be one of: {', '.join(GATES)}")
        if not ID_RE.fullmatch(holder):
            raise ProtocolError(f"pipeline[{index}].holder must be an actor slug")
        actor = db.get(Actor, holder)
        if actor is None or actor.disabled:
            raise ProtocolError(f"pipeline[{index}].holder: unknown actor {holder!r}")
        if gate == "queen" and actor.kind != "human":
            raise ProtocolError(f"pipeline[{index}].holder must be a human actor for queen gates")
        cleaned.append({"name": name, "holder": holder, "gate": gate})
    if len(cleaned) < 2:
        raise ProtocolError("a pipeline needs at least 2 stages")
    if cleaned[0]["gate"] == "queen":
        raise ProtocolError("the first stage cannot be a queen gate")
    return cleaned


def pipeline_of(task: Task) -> list[dict[str, Any]]:
    if not task.pipeline_json:
        raise ProtocolError(f"task has no pipeline: {task.id}")
    return json.loads(task.pipeline_json)


def _open_approval(db: Session, task: Task, stage_index: int, requested_by: str) -> tuple[Approval, dict[str, str]]:
    approve_token = new_token("rga")
    reject_token = new_token("rgr")
    approval = Approval(
        task_id=task.id,
        stage_index=stage_index,
        requested_by=requested_by,
        token_hash=hash_token(approve_token),
        reject_token_hash=hash_token(reject_token),
    )
    db.add(approval)
    db.flush()
    return approval, {"approve": approve_token, "reject": reject_token}


def _void_pending_approvals(
    db: Session,
    task_id: str,
    *,
    decided_by: str,
    note: str,
    exclude_id: int | None = None,
) -> None:
    approvals = db.execute(
        select(Approval).where(Approval.task_id == task_id, Approval.status == "pending")
    ).scalars()
    for approval in approvals:
        if approval.id == exclude_id:
            continue
        approval.status = "voided"
        approval.decided_by = decided_by
        approval.decision_note = note
        approval.decided_at = utcnow()
        approval.token_hash = ""
        approval.reject_token_hash = ""
    db.flush()


def _enter_stage(
    db: Session,
    task: Task,
    *,
    index: int,
    who: str,
    note: str,
) -> tuple[Approval, dict[str, str]] | None:
    """Move the baton into stage `index`; returns a fresh Approval for queen gates."""
    stages = pipeline_of(task)
    if not 0 <= index < len(stages):
        raise ProtocolError(f"pipeline stage out of range: {index}")
    stage = stages[index]
    update_task(
        db,
        task,
        who=who,
        is_privileged=True,
        status="handoff",
        holder=stage["holder"],
        progress=0,
        note=note,
        flow_driven=True,
    )
    task.pipeline_stage = index
    _void_pending_approvals(
        db,
        task.id,
        decided_by=who,
        note="pipeline moved away from a stale approval",
    )
    if stage["gate"] == "queen":
        return _open_approval(db, task, index, requested_by=who)
    db.flush()
    return None


def stage_done(
    db: Session,
    task: Task,
    *,
    who: str,
    is_privileged: bool,
    note: str,
    confidence: float | None = None,
) -> dict[str, Any]:
    """Current stage holder finishes their node and passes the baton on."""
    stages = pipeline_of(task)
    if not is_privileged and task.holder != who:
        raise ProtocolError(f"holder-only-writes: {who!r} does not hold {task.id}")
    if task.status != "doing":
        raise ProtocolError("先把卡转入 doing(开工)再交棒")
    index = task.pipeline_stage
    if stages[index]["gate"] == "queen":
        raise ProtocolError("queen 门节点由审批裁决,不能 stage-done")
    if not note or not note.strip():
        raise ProtocolError("交棒必须附回执备注")
    receipt = note.strip()
    if confidence is not None:
        if not 0 <= confidence <= 1:
            raise ProtocolError("confidence must be between 0 and 1")
        receipt = f"{receipt}(置信度 {confidence:.2f})"

    next_index = index + 1
    if next_index >= len(stages):
        update_task(
            db, task, who=who, is_privileged=True, status="done",
            note=f"节点「{stages[index]['name']}」完成,流程收官:{receipt}",
            flow_driven=True,
        )
        _void_pending_approvals(
            db,
            task.id,
            decided_by=who,
            note="pipeline completed",
        )
        return {"advanced_to": None, "approval": None, "done": True}

    entered = _enter_stage(
        db,
        task,
        index=next_index,
        who=who,
        note=f"节点「{stages[index]['name']}」完成,交棒「{stages[next_index]['name']}」:{receipt}",
    )
    approval, tokens = entered if entered is not None else (None, None)
    return {"advanced_to": next_index, "approval": approval,
            "approval_tokens": tokens, "done": False}


def stage_reject(
    db: Session,
    task: Task,
    *,
    who: str,
    is_privileged: bool,
    note: str,
) -> dict[str, Any]:
    """A review-stage holder sends the baton back to the previous stage."""
    stages = pipeline_of(task)
    if not is_privileged and task.holder != who:
        raise ProtocolError(f"holder-only-writes: {who!r} does not hold {task.id}")
    index = task.pipeline_stage
    if stages[index]["gate"] != "review":
        raise ProtocolError("只有 review 节点可以打回")
    if index == 0:
        raise ProtocolError("首节点无处可退")
    if not note or not note.strip():
        raise ProtocolError("打回必须说明原因")
    if task.status != "doing":
        raise ProtocolError("只有 doing 状态的 review 节点可以打回")
    prev = index - 1
    entered = _enter_stage(
        db,
        task,
        index=prev,
        who=who,
        note=f"节点「{stages[index]['name']}」打回「{stages[prev]['name']}」:{note.strip()}",
    )
    approval, tokens = entered if entered is not None else (None, None)
    return {
        "returned_to": prev, "approval": approval,
        "approval_tokens": tokens,
    }


def decide_approval(
    db: Session,
    approval: Approval,
    *,
    decided_by: str,
    decision: str,
    note: str = "",
) -> dict[str, Any]:
    """Settle a queen gate: approve advances (or completes), reject sends back."""
    if approval.status != "pending":
        raise ProtocolError(f"该审批已裁决过({approval.status})")
    if decision not in ("approve", "reject"):
        raise ProtocolError("decision must be approve or reject")
    task = db.get(Task, approval.task_id)
    if task is None:
        raise ProtocolError(f"task not found: {approval.task_id}")
    decider = db.get(Actor, decided_by)
    if decider is None or decider.disabled:
        raise ProtocolError(f"decided_by: unknown or disabled actor {decided_by!r}")
    stages = pipeline_of(task)
    index = approval.stage_index
    if not 0 <= index < len(stages):
        raise ProtocolError("审批指向了不存在的流程节点")
    stage = stages[index]
    if stage["gate"] != "queen":
        raise ProtocolError("审批不再指向 queen 门节点")
    if (task.pipeline_stage != index or task.status != "handoff"
            or task.holder != stage["holder"]):
        raise ProtocolError("任务状态已变化,该审批不再适用")

    target_status = "approved" if decision == "approve" else "rejected"
    settled = db.execute(
        update(Approval)
        .where(
            Approval.id == approval.id,
            Approval.status == "pending",
        )
        .values(
            status=target_status,
            decided_by=decided_by,
            decision_note=note.strip(),
            decided_at=utcnow(),
        )
        .execution_options(synchronize_session=False)
    )
    if settled.rowcount != 1:
        raise Conflict("该审批已被其他请求裁决")
    db.refresh(approval)

    suffix = f":{note.strip()}" if note.strip() else ""
    stage_name = stages[index]["name"]
    if decision == "approve":
        next_index = index + 1
        if next_index >= len(stages):
            update_task(
                db, task, who=decided_by, is_privileged=True, status="doing",
                note=f"女王门「{stage_name}」批准{suffix}",
                flow_driven=True,
            )
            update_task(
                db, task, who=decided_by, is_privileged=True, status="done",
                note="流程收官,交付",
                flow_driven=True,
            )
            _void_pending_approvals(
                db,
                task.id,
                decided_by=decided_by,
                note="pipeline completed",
            )
            return {"advanced_to": None, "done": True, "approval": None}
        entered = _enter_stage(
            db, task, index=next_index, who=decided_by,
            note=f"女王门「{stage_name}」批准,交棒「{stages[next_index]['name']}」{suffix}",
        )
        fresh, tokens = entered if entered is not None else (None, None)
        return {"advanced_to": next_index, "done": False, "approval": fresh,
                "approval_tokens": tokens}

    prev = index - 1
    entered = _enter_stage(
        db, task, index=prev, who=decided_by,
        note=f"女王门「{stage_name}」驳回,退回「{stages[prev]['name']}」{suffix}",
    )
    fresh, tokens = entered if entered is not None else (None, None)
    return {
        "returned_to": prev, "done": False, "approval": fresh,
        "approval_tokens": tokens,
    }


def approval_by_token(db: Session, token: str, decision: str) -> Approval | None:
    if decision not in ("approve", "reject"):
        return None
    column = (
        Approval.token_hash if decision == "approve" else Approval.reject_token_hash
    )
    return db.execute(select(Approval).where(column == hash_token(token))).scalar()



def approval_to_dict(approval: Approval, task: Task | None = None) -> dict[str, Any]:
    stage_name = None
    if task is not None and task.pipeline_json:
        stages = json.loads(task.pipeline_json)
        if 0 <= approval.stage_index < len(stages):
            stage_name = stages[approval.stage_index]["name"]
    return {
        "id": approval.id,
        "task_id": approval.task_id,
        "task_title": task.title if task else None,
        "stage_index": approval.stage_index,
        "stage_name": stage_name,
        "status": approval.status,
        "requested_by": approval.requested_by,
        "decided_by": approval.decided_by,
        "decision_note": approval.decision_note,
        "created_at": approval.created_at.isoformat() if approval.created_at else None,
        "decided_at": approval.decided_at.isoformat() if approval.decided_at else None,
    }
