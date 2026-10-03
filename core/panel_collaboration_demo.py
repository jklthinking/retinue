"""Synthetic collaboration, seeded through the same services as live reports."""

from __future__ import annotations

import datetime as dt

from sqlalchemy import select
from sqlalchemy.orm import Session

from server.collaboration import delegate, report_run, set_delegation_policy, start_run
from server.collaboration_schemas import DelegationBody, DelegationPolicyBody, RunCreateBody, RunEventBody
from server.db import Actor, Task, TokenUsage, utcnow
from server.deps import Principal
from server.engine import append_attempt, create_task, update_task


def seed_collaboration_demo(db: Session, advance_clock) -> str:
    """One coordinator, a completed branch, and a branch waiting for a reviewer."""
    for actor_id, node, runtime, model in (
        ("analyst", "demo-desktop-a", "codex", "demo-coordinator"),
        ("dev-assist", "demo-build-b", "claude-code", "demo-dev-assist"),
        ("copywriter", "demo-desktop-c", "codex", "demo-copywriter"),
    ):
        actor = db.get(Actor, actor_id)
        actor.node, actor.runtime, actor.model = node, runtime, model
        # These are synthetic fixtures from seed_demo, not imported runtime data.
        # Keep their attribution consistent with this scenario's worker identity.
        for usage in db.scalars(select(TokenUsage).where(TokenUsage.actor_id == actor_id)):
            usage.runtime = runtime
    operator = Principal(kind="user", name="pm", actor_id="pm", role="admin")
    coordinator = Principal(kind="agent", name="analyst", actor_id="analyst", role="agent")
    root = create_task(db, title="协作示例：产品发布方案", created_by="pm", holder="analyst",
                       dept="市场", priority="urgent", acceptance=["资料核对完成", "文案通过人工审阅"],
                       note="合成演示：由分析智能体协调两个独立分支")
    set_delegation_policy(db, root, operator, DelegationPolicyBody(
        allowed_actor_ids=["analyst", "copywriter", "dev-assist"], max_depth=2, max_children=4,
        idempotency_key="demo-policy-42"))
    update_task(db, root, who="analyst", is_privileged=False, status="doing", note="接单，拆分资料核对与发布文案")
    root_run = start_run(db, root, coordinator, RunCreateBody(
        title="协调发布方案", model="demo-coordinator", module="发布统筹", session_ref="demo-coordinator-42",
        lease_term=root.lease_term, idempotency_key="demo-root-run-42"))["run"]

    def branch(actor_id: str, title: str, instruction: str, acceptance: list[str], key: str, module: str):
        result = delegate(db, root, coordinator, DelegationBody(
            delegated_to=actor_id, title=title, instruction=instruction, acceptance=acceptance, module=module,
            parent_run_id=root_run["id"], lease_term=root.lease_term, idempotency_key=key))
        child = db.get(Task, result["delegation"]["child_task_id"])
        actor = Principal(kind="agent", name=actor_id, actor_id=actor_id, role="agent")
        update_task(db, child, who=actor_id, is_privileged=False, status="doing", note="已接收委派并开始执行")
        run = start_run(db, child, actor, RunCreateBody(
            title=title, model=f"demo-{actor_id}", module=module, session_ref=f"demo-{actor_id}-42",
            lease_term=child.lease_term, idempotency_key=f"{key}-run"))["run"]
        return child, actor, run

    # A deterministic synthetic timeline exercises duration rendering rather
    # than making every sample report occur at the same instant.
    advance_clock(10)
    research, researcher, research_run = branch("dev-assist", "核对产品功能与发布资料",
        "核对三项产品功能，交付可追溯的核对结果", ["三项功能核对完成", "交付核对表"], "demo-research-42", "功能核对")
    advance_clock(35)
    end = utcnow()
    attempt, _ = append_attempt(db, research, reporter_kind="actor", reporter_id=researcher.name,
        duty=None, outcome="succeeded", started_at=dt.datetime.fromisoformat(research_run["started_at"].replace("Z", "+00:00")), ended_at=end,
        reason=None, exit_status=None, idempotency_key="demo-research-attempt-42",
        lease_term=research.lease_term, session_ref="demo-dev-assist-42")
    report_run(db, research, researcher, research_run["id"], RunEventBody(
        status="succeeded", note="三项功能已核对，核对表已交付", progress={"completed": 3, "total": 3, "unit": "核对项"},
        progress_report={"completed": [{"summary": "三项功能核对", "refs": ["artifact:demo-feature-checklist"], "revision": "demo-v1"}],
                         "remaining": [], "next_action": "审阅发布文案", "next_owner": "pm"},
        refs=["artifact:demo-feature-checklist"], attempt_id=attempt.attempt_key,
        lease_term=research.lease_term, idempotency_key="demo-research-done-42"))
    update_task(db, research, who=researcher.name, is_privileged=False, status="done",
                note="核对分支完成", refs=["artifact:demo-feature-checklist"], lease_term=research.lease_term)

    advance_clock(5)
    writing, writer, writing_run = branch("copywriter", "撰写发布文案并提交审阅",
        "根据核对资料撰写发布文案，等待项目经理确认后交付", ["完成首屏标题", "完成功能说明", "人工审阅通过"], "demo-writing-42", "发布文案")
    advance_clock(10)
    report_run(db, writing, writer, writing_run["id"], RunEventBody(
        status="waiting", note="文案初稿已完成，等待项目经理确认语气",
        progress={"completed": 2, "total": 3, "unit": "检查项"},
        progress_report={"completed": [{"summary": "首屏标题与功能说明", "refs": ["artifact:demo-release-draft"], "revision": "demo-v1"}],
                         "remaining": ["人工审阅通过"], "next_action": "确认语气与用词", "next_owner": "pm"},
        waiting={"kind": "review", "owner": "pm", "reason": "请确认发布文案的语气与用词"},
        refs=["artifact:demo-release-draft"], lease_term=writing.lease_term,
        idempotency_key="demo-writing-wait-42"))
    advance_clock(5)
    report_run(db, root, coordinator, root_run["id"], RunEventBody(
        status="waiting", note="资料核对已完成，汇总前等待文案审阅",
        progress={"completed": 1, "total": 2, "unit": "交付分支"},
        progress_report={"completed": [{"summary": "核对分支已交付", "refs": ["artifact:demo-feature-checklist"], "revision": "demo-v1"}],
                         "remaining": ["发布文案完成审阅", "汇总发布方案"], "next_action": "接收审阅后的文案", "next_owner": "copywriter"},
        waiting={"kind": "dependency", "owner": "copywriter", "reason": "等待发布文案完成审阅"},
        lease_term=root.lease_term, idempotency_key="demo-root-wait-42"))
    return root.id
