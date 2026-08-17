"""Shared presenters, notifiers, and read-model builders for the routers.

Everything here is a plain function of its arguments — the closures these
used to live in captured the whole application, which is exactly what this
split removes.
"""

from __future__ import annotations

import datetime as dt
import json
from typing import Any

from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from . import feishu_cards
from .db import (
    Actor,
    KnowledgeSource,
    Node,
    PipelineTemplate,
    RuntimeSession,
    Skill,
    Task,
    TaskEvent,
    utcnow,
)
from .deps import ONLINE_WINDOW, Principal
from .engine import task_to_dict
from .flow import pipeline_of


def receipt_text(task: dict[str, Any]) -> str:
    if not task.get("chain"):
        return f"【任务回执】{task['id']} {task['title']}"
    event = task["chain"][-1]
    payload = event.get("payload")
    attribution = (
        payload.get("acted_on_behalf_of") if isinstance(payload, dict) else None
    )
    execution = ""
    if isinstance(attribution, dict):
        authority = attribution.get("authorising_identity")
        performer = attribution.get("performing_agent")
        if isinstance(authority, str) and isinstance(performer, str):
            execution = f"　执行:{performer} 代表 {authority}"
    return (
        f"【任务回执】{task['id']} {task['title']}\n"
        f"状态:{event.get('from_status') or '—'} → {event.get('to_status') or task['status']}　"
        f"持棒:{event.get('from_holder') or '—'} → {event.get('to_holder') or task['holder']}　"
        f"备注:{event['did']}{execution}"
    )


def task_response(task: Task) -> dict[str, Any]:
    """Return a task plus the canonical two-line receipt for automation clients."""
    result = task_to_dict(task)
    result["receipt"] = receipt_text(result)
    return result


def notify_feishu(text: str) -> None:
    """Fire-and-forget group webhook via the notifier plugin; off unless configured."""
    from .notify import (
        fire_and_forget_group_webhook,
        legacy_group_webhook_payload_text,
    )

    fire_and_forget_group_webhook(legacy_group_webhook_payload_text(text))


def actor_to_dict(actor: Actor, online_cutoff: dt.datetime) -> dict[str, Any]:
    last_seen = actor.last_seen_at
    return {
        "id": actor.id,
        "kind": actor.kind,
        "display_name": actor.display_name,
        "role": actor.role,
        "goal": actor.goal,
        "runtime": actor.runtime,
        "model": actor.model,
        "node": actor.node,
        "disabled": actor.disabled,
        "last_seen_at": last_seen.isoformat() if last_seen else None,
        "online": bool(last_seen and last_seen > online_cutoff.replace(tzinfo=None)),
    }


def actor_name(db: Session, actor_id: str) -> str:
    actor = db.get(Actor, actor_id)
    return actor.display_name if actor and actor.display_name else actor_id


def get_task_or_404(db: Session, task_id: str) -> Task:
    task = db.get(Task, task_id)
    if task is None:
        raise HTTPException(status_code=404, detail=f"task not found: {task_id}")
    return task


def post_flow_cards(db: Session, task: Task, outcome: dict[str, Any]) -> None:
    """Send the right Feishu card for a flow movement."""
    snapshot = task_to_dict(task)
    if outcome.get("done"):
        feishu_cards.send_delivery_card(snapshot)
        return
    stages = pipeline_of(task)
    approval = outcome.get("approval")
    if approval is not None:
        tokens = outcome.get("approval_tokens") or {}
        if not tokens.get("approve") or not tokens.get("reject"):
            raise RuntimeError("fresh approval did not include decision-bound tokens")
        feishu_cards.send_decision_card(
            snapshot, stages[task.pipeline_stage]["name"], approval.id,
            tokens["approve"], tokens["reject"],
        )
    elif outcome.get("advanced_to") is not None or outcome.get("returned_to") is not None:
        index = task.pipeline_stage
        feishu_cards.send_baton_card(
            snapshot, stages[index]["name"], actor_name(db, task.holder)
        )


def build_orientation_context(db: Session, principal: Principal) -> dict[str, Any]:
    """Build a safe, refreshable orientation packet for a newly connected agent.

    This is deliberately a read model: it contains operational metadata and
    current task summaries, never transcripts, prompts, private memory, or keys.
    """
    generated_at = utcnow().isoformat()
    cutoff = utcnow() - ONLINE_WINDOW
    task_counts = dict(
        db.execute(
            select(Task.status, func.count())
            .where(Task.archived.is_(False))
            .group_by(Task.status)
        ).all()
    )
    actors = [
        {
            "id": actor.id,
            "kind": actor.kind,
            "display_name": actor.display_name,
            "role": actor.role,
            "goal": actor.goal,
            "runtime": actor.runtime,
            "model": actor.model,
            "node": actor.node,
            "online": bool(
                actor.last_seen_at
                and actor.last_seen_at > cutoff.replace(tzinfo=None)
            ),
        }
        for actor in db.execute(select(Actor).order_by(Actor.kind, Actor.id)).scalars()
    ]
    nodes = []
    for node in db.execute(
        select(Node)
        .where(Node.membership_status == "admitted")
        .order_by(Node.id)
    ).scalars():
        try:
            services = json.loads(node.services_json or "[]")
        except (TypeError, json.JSONDecodeError):
            services = []
        nodes.append(
            {
                "id": node.id,
                "label": node.label,
                "hostname": node.hostname,
                "platform": node.platform,
                "updated_at": node.updated_at.isoformat() if node.updated_at else None,
                "healthy_services": [
                    str(item.get("name") or item.get("unit") or item.get("service"))
                    for item in services
                    if isinstance(item, dict)
                    and item.get("healthy", item.get("status") in {"active", "ok", "running"})
                ],
            }
        )
    recent_tasks = []
    recent = db.execute(
        select(Task)
        .where(Task.archived.is_(False))
        .order_by(Task.updated_at.desc(), Task.id.desc())
        .limit(12)
    ).scalars()
    for task in recent:
        recent_tasks.append(
            {
                "id": task.id,
                "title": task.title,
                "status": task.status,
                "holder": task.holder,
                "dept": task.dept,
                "progress": task.progress,
                "updated_at": task.updated_at.isoformat() if task.updated_at else None,
            }
        )
    skills = [
        {
            "name": skill.name,
            "category": skill.category,
            "description": skill.description,
        }
        for skill in db.execute(
            select(Skill).where(Skill.enabled.is_(True)).order_by(Skill.category, Skill.name).limit(80)
        ).scalars()
    ]
    knowledge_sources = [
        {"name": source.name, "kind": source.kind, "docs": source.docs}
        for source in db.execute(select(KnowledgeSource).order_by(KnowledgeSource.name)).scalars()
    ]
    pipeline_templates = []
    for template in db.execute(select(PipelineTemplate).order_by(PipelineTemplate.name)).scalars():
        try:
            stages = json.loads(template.stages_json or "[]")
        except (TypeError, json.JSONDecodeError):
            stages = []
        pipeline_templates.append(
            {
                "name": template.name,
                "stages": [
                    {
                        "name": str(stage.get("name") or stage.get("title") or "stage"),
                        "gate": stage.get("gate") or stage.get("approval") or "none",
                    }
                    for stage in stages
                    if isinstance(stage, dict)
                ],
            }
        )
    status = {
        "task_counts": task_counts,
        "actors": len(actors),
        "online_actors": sum(1 for actor in actors if actor["online"]),
        "skills": len(skills),
        "nodes": len(nodes),
        "knowledge_sources": len(knowledge_sources),
    }
    rules = [
        "任务卡是唯一工作单元；当前持棒者负责下一步并留下回执。",
        "交棒、打回、审核和人工审批都写入同一条任务链，不复制第二份状态。",
        "新成员先读本上下文包，再用自己的身份领取任务；不代替其他成员写入。",
    ]
    boundary = {
        "included": ["组织使命与规则", "成员与节点目录", "当前任务摘要", "技能与流程目录"],
        "excluded": ["会话正文", "系统提示词与私有记忆", "密码、令牌与外部平台密钥", "未授权的原始知识库内容"],
    }
    catalog = build_data_catalog(db)
    markdown_lines = [
        "# RETINUE 组织上下文包",
        "",
        f"生成时间：{generated_at}",
        f"接入身份：{principal.actor_id or principal.name}",
        "",
        "## 组织使命",
        "让人类指令在可追溯的任务卡、执行、审核、决策与交付链中完成。",
        "",
        "## 工作规则",
        *[f"- {rule}" for rule in rules],
        "",
        "## 当前概览",
        f"- 任务：{status['task_counts']}",
        f"- 成员：{status['actors']}（在线 {status['online_actors']}）",
        f"- 节点：{status['nodes']}；技能：{status['skills']}；知识源：{status['knowledge_sources']}",
        f"- 数据质量：{catalog['quality']['score']}%（详见 GET /api/data-catalog）",
        "",
        "## 刷新方式",
        "每次启动或收到新任务时，用自己的 Bearer 令牌 GET /api/orientation/context；不要把旧上下文当成事实。",
        "",
        "## 隐私边界",
        "- 包含：组织规则、目录和脱敏任务摘要。",
        "- 不包含：会话正文、私有记忆、提示词、密码、令牌和外部平台密钥。",
    ]
    return {
        "schema_version": "retinue-internal-context/v1",
        "generated_at": generated_at,
        "audience": {"actor_id": principal.actor_id, "name": principal.name, "role": principal.role},
        "mission": "让人类指令在可追溯的任务卡、执行、审核、决策与交付链中完成。",
        "rules": rules,
        "bootstrap": [
            "GET /api/auth/me",
            "GET /api/orientation/context",
            "GET /api/tasks?holder=<actor_id>",
            "POST /api/tasks/{task_id}/update（只更新自己持有的任务）",
        ],
        "status": status,
        "data_quality": {
            "score": catalog["quality"]["score"],
            "recommendations": catalog["recommendations"][:3],
            "schema_version": catalog["schema_version"],
        },
        "actors": actors,
        "nodes": nodes,
        "recent_tasks": recent_tasks,
        "skills": skills,
        "knowledge_sources": knowledge_sources,
        "pipeline_templates": pipeline_templates,
        "privacy_boundary": boundary,
        "markdown": "\n".join(markdown_lines),
    }


def build_data_catalog(db: Session) -> dict[str, Any]:
    """Return a governed catalog of Retinue's storage layers, not raw content."""
    generated_at = utcnow().isoformat()
    tasks = list(db.execute(select(Task).where(Task.archived.is_(False))).scalars())
    actors = list(db.execute(select(Actor).order_by(Actor.id)).scalars())
    skills = list(db.execute(select(Skill).order_by(Skill.category, Skill.name)).scalars())
    nodes = list(
        db.execute(
            select(Node)
            .where(Node.membership_status == "admitted")
            .order_by(Node.id)
        ).scalars()
    )
    sources = list(db.execute(select(KnowledgeSource).order_by(KnowledgeSource.name)).scalars())
    sessions = db.execute(select(func.count()).select_from(RuntimeSession)).scalar() or 0
    events = db.execute(select(func.count()).select_from(TaskEvent)).scalar() or 0
    templates = db.execute(select(func.count()).select_from(PipelineTemplate)).scalar() or 0
    task_total = len(tasks)
    acceptance_count = sum(1 for task in tasks if task.acceptance)
    holder_count = sum(1 for task in tasks if task.holder and db.get(Actor, task.holder))
    active_agents = [
        actor for actor in actors if actor.kind == "agent" and not actor.disabled
    ]
    active_humans = [
        actor for actor in actors if actor.kind == "human" and not actor.disabled
    ]
    runtime_count = sum(1 for actor in active_agents if actor.runtime.strip())
    contact_count = sum(
        1 for actor in active_humans if actor.runtime.strip() or actor.node.strip()
    )
    dept_count = sum(1 for task in tasks if task.dept)
    refs_count = sum(1 for task in tasks if task.refs)
    checks = [
        {
            "key": "task_acceptance",
            "label": "任务验收条件",
            "observed": acceptance_count,
            "total": task_total,
            "status": "good" if acceptance_count == task_total else "attention",
            "detail": "新任务尽量把可观察结果写进 acceptance。",
        },
        {
            "key": "task_holder",
            "label": "任务持棒者",
            "observed": holder_count,
            "total": task_total,
            "status": "good" if holder_count == task_total else "attention",
            "detail": "任务必须绑定已登记的 Actor，才可安全交棒。",
        },
        {
            "key": "task_department",
            "label": "任务业务域",
            "observed": dept_count,
            "total": task_total,
            "status": "good" if dept_count == task_total else "info",
            "detail": "dept 用于分组、路由和结果统计；可先为空。",
        },
        {
            "key": "task_refs",
            "label": "任务证据引用",
            "observed": refs_count,
            "total": task_total,
            "status": "good" if refs_count == task_total else "info",
            "detail": "refs 连接文档、交付物或外部证据，不复制正文。",
        },
        {
            "key": "actor_runtime",
            "label": "执行型智能体运行时",
            "observed": runtime_count,
            "total": len(active_agents),
            "status": "good" if runtime_count == len(active_agents) else "attention",
            "detail": "仅执行型智能体需要 runtime；它用于匹配、会话映射和节点健康判断。",
        },
        {
            "key": "human_contact",
            "label": "人类角色接入点",
            "observed": contact_count,
            "total": len(active_humans),
            "status": "good" if contact_count == len(active_humans) else "attention",
            "detail": "人类角色登记沟通入口或所属节点，不伪装成模型运行时。",
        },
        {
            "key": "session_index",
            "label": "会话元数据索引",
            "observed": 1 if sessions else 0,
            "total": 1,
            "status": "good" if sessions else "attention",
            "detail": "仅同步运行时明确授权的元数据、摘要或脱敏消息；运行时仍是会话正文的唯一权威。",
        },
        {
            "key": "pipeline_templates",
            "label": "标准流水线模板",
            "observed": min(templates, 3),
            "total": 3,
            "status": "good" if templates >= 3 else "attention",
            "detail": "至少保留讲义、研发交付、知识整理三条可复用流程。",
        },
        {
            "key": "event_chain",
            "label": "任务事件链",
            "observed": events,
            "total": task_total,
            "status": "good" if events >= task_total else "attention",
            "detail": "事件链是状态变化的审计事实，不与看板重复双写。",
        },
    ]
    weighted = [item for item in checks if item["status"] != "info"]
    score = round(100 * sum(item["status"] == "good" for item in weighted) / max(len(weighted), 1))
    recommendations = []
    if acceptance_count < task_total:
        recommendations.append(f"为 {task_total - acceptance_count} 张任务卡补充可验证的 acceptance。")
    if runtime_count < len(active_agents):
        recommendations.append(f"为 {len(active_agents) - runtime_count} 个执行型智能体补齐 runtime。")
    if contact_count < len(active_humans):
        recommendations.append(f"为 {len(active_humans) - contact_count} 名人类角色登记接入点或所属节点。")
    if sessions == 0:
        recommendations.append("会话索引目前为 0；先接入 metadata 级索引，不导入会话正文。")
    if templates < 3:
        recommendations.append("流程模板不足 3 条；请固定讲义、研发交付、知识整理三条模板。")
    if not recommendations:
        recommendations.append("当前结构满足试点运行；下一步做字段级契约测试和 7 天数据质量观察。")
    return {
        "schema_version": "retinue-data-catalog/v1",
        "generated_at": generated_at,
        "storage_contract": {
            "documents": "Markdown + YAML frontmatter + 双向链接",
            "operational": "SQLite 关系表（任务、成员、节点、技能、知识源、事件链）",
            "json_fields": [
                "tasks.acceptance_json",
                "tasks.refs_json",
                "tasks.pipeline_json",
                "skills.owners_json",
                "skills.source_snapshot_json",
                "skill_binding_events.payload_json",
            ],
            "canonical": "任务状态以 tasks + task_events 为准；技能绑定以 skill_bindings + skill_binding_events 为准；文档正文仍以 Obsidian 为准。",
        },
        "summary": {
            "tasks": task_total,
            "actors": len(actors),
            "skills": len(skills),
            "nodes": len(nodes),
            "knowledge_sources": len(sources),
            "sessions": sessions,
            "events": events,
            "pipeline_templates": templates,
            "quality_score": score,
        },
        "layers": [
            {"key": "tasks", "title": "任务与流程", "table": "tasks / task_events", "rows": task_total, "status": "good", "fields": ["title", "dept", "holder", "status", "acceptance", "refs", "progress"]},
            {"key": "actors", "title": "智能体与成员", "table": "actors / users / api_tokens", "rows": len(actors), "status": "good", "fields": ["id", "kind", "role", "goal", "runtime", "model", "node", "online"]},
            {"key": "skills", "title": "技能注册", "table": "skills / skill_bindings / skill_binding_events", "rows": len(skills), "status": "good", "fields": ["name", "category", "owners", "source", "source_kind", "imported_by", "enabled"]},
            {"key": "nodes", "title": "节点与服务", "table": "nodes / node_tokens", "rows": len(nodes), "status": "good", "fields": ["id", "platform", "disk", "memory", "services", "updated_at"]},
            {"key": "knowledge", "title": "知识源目录", "table": "knowledge_sources + Obsidian", "rows": len(sources), "status": "good", "fields": ["name", "kind", "docs", "updated_at"]},
            {"key": "sessions", "title": "会话索引", "table": "runtime_sessions", "rows": sessions, "status": "info" if sessions == 0 else "good", "fields": ["actor_id", "runtime", "privacy", "cursor", "task_id"]},
        ],
        "quality": {"score": score, "checks": checks},
        "recommendations": recommendations,
        "privacy": {
            "web_catalog": "只返回字段、数量、状态和目录元数据",
            "excluded": ["会话正文", "系统提示词", "私有记忆", "密码和令牌", "Obsidian 原始正文"],
        },
    }
