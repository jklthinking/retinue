"""Approval (queen gate) routes, including the signed-link confirmation pages."""

from __future__ import annotations

import html
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import HTMLResponse
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError, OperationalError
from sqlalchemy.orm import Session

from core.protocol.task import ProtocolError

from ..db import Approval, Task
from ..deps import Principal, get_db, require_admin, require_auth, wrap_protocol_errors
from ..flow import approval_by_token, approval_to_dict, decide_approval, pipeline_of
from ..helpers import (
    get_task_or_404,
    notify_feishu,
    post_flow_cards,
    receipt_text,
    task_response,
)
from ..engine import task_to_dict
from ..schemas import DecideBody

router = APIRouter()


@router.get("/api/approvals")
def get_approvals(
    pending: bool = False,
    task_id: str | None = None,
    principal: Principal = Depends(require_auth),
    db: Session = Depends(get_db, scope="function"),
) -> list[dict[str, Any]]:
    query = select(Approval).order_by(Approval.created_at.desc())
    if pending:
        query = query.where(Approval.status == "pending")
    if task_id:
        query = query.where(Approval.task_id == task_id)
    approvals = list(db.execute(query).scalars())
    return [approval_to_dict(a, db.get(Task, a.task_id)) for a in approvals[:100]]


@router.post("/api/approvals/{approval_id}/decide")
def post_approval_decide(
    approval_id: int,
    body: DecideBody,
    principal: Principal = Depends(require_admin),
    db: Session = Depends(get_db, scope="function"),
) -> dict[str, Any]:
    approval = db.get(Approval, approval_id)
    if approval is None:
        raise HTTPException(status_code=404, detail="approval not found")
    task = get_task_or_404(db, approval.task_id)
    try:
        outcome = decide_approval(
            db,
            approval,
            decided_by=principal.write_identity,
            decision=body.decision,
            note=body.note,
        )
    except ProtocolError as exc:
        raise wrap_protocol_errors(exc) from exc
    except (IntegrityError, OperationalError) as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail="审批刚被其他请求处理") from exc
    post_flow_cards(db, task, outcome)
    result = task_response(task)
    notify_feishu(result["receipt"])
    return result


def approval_page(
    title: str,
    body: str,
    *,
    ok: bool,
    confirm_decision: str | None = None,
) -> HTMLResponse:
    color = "#1fa593" if ok else "#d64545"
    action = ""
    if confirm_decision is not None:
        label = "确认批准" if confirm_decision == "approve" else "确认驳回"
        action = (
            "<form method='post'><button type='submit' style='border:0;border-radius:8px;"
            f"padding:10px 18px;background:{color};color:white;font-weight:700'>"
            f"{html.escape(label)}</button></form>"
        )
    document = (
        "<!doctype html><meta charset='utf-8'><meta name='viewport' "
        "content='width=device-width,initial-scale=1'><body style=\"font:15px/1.7 "
        "system-ui;background:#faf7f2;color:#0b1d3a;display:grid;"
        "place-items:center;min-height:96vh\"><main style='text-align:center;max-width:36rem;"
        "padding:2rem'><h2 style='color:"
        f"{color}'>{html.escape(title)}</h2><p>{html.escape(body)}</p>{action}</main>"
        "</body></html>"
    )
    return HTMLResponse(
        document,
        headers={
            "Cache-Control": "no-store",
            "Pragma": "no-cache",
            "Referrer-Policy": "no-referrer",
            "X-Content-Type-Options": "nosniff",
            "Content-Security-Policy": (
                "default-src 'none'; style-src 'unsafe-inline'; "
                "form-action 'self'; base-uri 'none'"
            ),
        },
    )


def linked_approval(
    db: Session, approval_id: int, token: str, decision: str
) -> tuple[Approval | None, HTMLResponse | None]:
    if decision not in ("approve", "reject"):
        return None, approval_page("参数错误", "未知的裁决类型。", ok=False)
    approval = approval_by_token(db, token, decision)
    if approval is None or approval.id != approval_id:
        return None, approval_page("链接无效", "令牌不匹配或已失效。", ok=False)
    if approval.status != "pending":
        return None, approval_page(
            "已经裁决过了",
            f"该审批此前已被 {approval.decided_by or '—'} 裁决为 {approval.status}。",
            ok=False,
        )
    return approval, None


@router.get("/api/approvals/{approval_id}/act", response_class=HTMLResponse)
def approval_act_confirm(
    approval_id: int,
    token: str,
    decision: str,
    db: Session = Depends(get_db, scope="function"),
) -> HTMLResponse:
    """Render a confirmation page. GET never mutates approval state."""
    approval, error_page = linked_approval(db, approval_id, token, decision)
    if error_page is not None:
        return error_page
    assert approval is not None
    task = db.get(Task, approval.task_id)
    if task is None:
        return approval_page("任务不存在", approval.task_id, ok=False)
    verb = "批准" if decision == "approve" else "驳回"
    return approval_page(
        f"确认{verb}",
        f"{task.id} {task.title}。此页面不会自动裁决，请点击下方按钮确认。",
        ok=decision == "approve",
        confirm_decision=decision,
    )


@router.post("/api/approvals/{approval_id}/act", response_class=HTMLResponse)
def approval_act_submit(
    approval_id: int,
    token: str,
    decision: str,
    db: Session = Depends(get_db, scope="function"),
) -> HTMLResponse:
    """Settle a decision-bound signed link after explicit human confirmation."""
    approval, error_page = linked_approval(db, approval_id, token, decision)
    if error_page is not None:
        return error_page
    assert approval is not None
    task = db.get(Task, approval.task_id)
    if task is None:
        return approval_page("任务不存在", approval.task_id, ok=False)
    try:
        stages = pipeline_of(task)
        if not 0 <= approval.stage_index < len(stages):
            raise ProtocolError("审批指向了不存在的流程节点")
        decided_by = stages[approval.stage_index]["holder"]
        outcome = decide_approval(
            db,
            approval,
            decided_by=decided_by,
            decision=decision,
            note="经飞书确认页裁决",
        )
    except ProtocolError as exc:
        db.rollback()
        return approval_page("无法裁决", str(exc), ok=False)
    except (IntegrityError, OperationalError):
        db.rollback()
        return approval_page("未执行", "该审批刚被其他请求处理，请刷新确认。", ok=False)
    post_flow_cards(db, task, outcome)
    notify_feishu(receipt_text(task_to_dict(task)))
    verb = "已批准 ✅" if decision == "approve" else "已驳回 ❌"
    return approval_page(verb, f"{task.id} {task.title}", ok=True)
