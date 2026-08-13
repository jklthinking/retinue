"""Explainable, offline agent matching for the web collaboration surface."""

from __future__ import annotations

import datetime as dt
import json
import re
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from .db import Actor, Node, NodeRuntime, Skill, Task, utcnow
from .skill_ops import bound_skills_by_actor


ACTIVE_STATUSES = {"queued", "doing", "handoff", "blocked"}
RUNTIME_PROBE_MAX_AGE = dt.timedelta(hours=24)
_COMPACT_RE = re.compile(r"[^0-9a-z\u4e00-\u9fff]+")


def _compact(value: str) -> str:
    return _COMPACT_RE.sub("", value.casefold())


def _bigrams(value: str) -> set[str]:
    compact = _compact(value)
    if len(compact) < 2:
        return {compact} if compact else set()
    return {compact[index : index + 2] for index in range(len(compact) - 1)}


def _relevance(query: str, text: str) -> float:
    """Return a deterministic 0..1 relevance score that works for Chinese and English."""
    wanted = _compact(query)
    candidate = _compact(text)
    if not wanted or not candidate:
        return 0.0
    if wanted in candidate:
        return 1.0
    if candidate in wanted and len(candidate) >= 2:
        return min(1.0, 0.65 + len(candidate) / max(len(wanted), 1))
    wanted_pairs = _bigrams(wanted)
    shared = wanted_pairs & _bigrams(candidate)
    return min(1.0, len(shared) / max(1, min(4, len(wanted_pairs))))


def _skill_owners(skill: Skill) -> list[str]:
    try:
        owners = json.loads(skill.owners_json or "[]")
    except (TypeError, json.JSONDecodeError):
        return []
    return [str(owner) for owner in owners if isinstance(owner, str)]


def _naive_utc(value: dt.datetime) -> dt.datetime:
    if value.tzinfo is None:
        return value
    return value.astimezone(dt.timezone.utc).replace(tzinfo=None)


def _runtime_evidence(
    actor: Actor,
    nodes_by_id: dict[str, Node],
    runtimes_by_pair: dict[tuple[str, str], NodeRuntime],
    probe_cutoff: dt.datetime,
) -> tuple[int, str]:
    """Score a claimed binding only when the measured inventory supports it."""
    runtime = actor.runtime.strip()
    node_id = actor.node.strip()
    if not runtime:
        return -6, "运行时证据：未声明运行时，无法核验可执行环境（-6）"
    if not node_id:
        return -6, f"运行时证据：{runtime} 缺少节点，无法查验清单（-6）"

    node = nodes_by_id.get(node_id)
    probed_at = node.runtimes_probed_at if node is not None else None
    if probed_at is None:
        return -4, f"运行时证据：节点 {node_id} 从未探测，{runtime} 尚未确认（-4）"
    if _naive_utc(probed_at) <= probe_cutoff:
        return (
            -4,
            f"运行时证据：节点 {node_id} 的探针已过期，{runtime} 不算确认；请重新探测（-4）",
        )

    inventory = runtimes_by_pair.get((node_id, runtime))
    if inventory is None or not inventory.available:
        return (
            -8,
            f"运行时证据：节点 {node_id} 的最新清单未确认 {runtime}；请核对绑定或安装（-8）",
        )
    return (
        12,
        f"运行时证据：{runtime} 已由节点 {node_id} 的新鲜探针确认（{inventory.source}，+12）",
    )


def match_agents(
    db: Session,
    query: str,
    *,
    limit: int = 8,
    online_cutoff: dt.datetime | None = None,
    runtime_probe_cutoff: dt.datetime | None = None,
) -> list[dict[str, Any]]:
    """Rank enabled agents using purpose, skills, measured runtime evidence, and history.

    The matcher intentionally stays offline and explainable. It does not send the
    user's task text to an embedding provider.
    """
    cutoff = (online_cutoff or (utcnow() - dt.timedelta(minutes=15))).replace(tzinfo=None)
    probe_cutoff = (
        runtime_probe_cutoff or (utcnow() - RUNTIME_PROBE_MAX_AGE)
    ).replace(tzinfo=None)
    agents = list(
        db.execute(
            select(Actor)
            .where(Actor.kind == "agent")
            .where(Actor.disabled.is_(False))
            .order_by(Actor.id)
        ).scalars()
    )
    skills = list(
        db.execute(
            select(Skill).where(Skill.enabled.is_(True)).order_by(Skill.name)
        ).scalars()
    )
    tasks = list(
        db.execute(select(Task).where(Task.archived.is_(False))).scalars()
    )
    nodes_by_id = {
        node.id: node
        for node in db.execute(
            select(Node).where(Node.membership_status == "admitted")
        ).scalars()
    }
    runtimes_by_pair = {
        (runtime.node_id, runtime.runtime): runtime
        for runtime in db.execute(select(NodeRuntime)).scalars()
        if runtime.node_id in nodes_by_id
    }

    skills_by_owner: dict[str, list[Skill]] = bound_skills_by_actor(db)
    bound_actors = set(skills_by_owner)
    for skill in skills:
        for owner in _skill_owners(skill):
            if owner not in bound_actors:
                skills_by_owner.setdefault(owner, []).append(skill)

    ranked: list[dict[str, Any]] = []
    has_query = bool(_compact(query))
    for actor in agents:
        owned = skills_by_owner.get(actor.id, [])
        active = sum(
            1
            for task in tasks
            if task.holder == actor.id and task.status in ACTIVE_STATUSES
        )
        completed = sum(
            1 for task in tasks if task.holder == actor.id and task.status == "done"
        )
        online = bool(
            actor.last_seen_at and _naive_utc(actor.last_seen_at) > cutoff
        )

        skill_rows = [
            (
                _relevance(
                    query,
                    " ".join(
                        [skill.name, skill.category, skill.description]
                    ),
                ),
                skill,
            )
            for skill in owned
        ]
        skill_rows.sort(key=lambda row: (row[0], row[1].name), reverse=True)
        top_skill = skill_rows[0][0] if skill_rows else 0.0
        supporting = sum(score for score, _skill in skill_rows[1:3])
        role = actor.role.strip()
        goal = actor.goal.strip()
        role_relevance = _relevance(query, role)
        goal_relevance = _relevance(query, goal)
        role_points = round(role_relevance * 6) if has_query else 0
        goal_points = round(goal_relevance * 9) if has_query else 0
        legacy_identity_points = 0
        if has_query and not role and not goal:
            legacy_identity_points = round(
                _relevance(
                    query,
                    " ".join(
                        [
                            actor.id,
                            actor.display_name,
                            actor.runtime,
                            actor.model,
                        ]
                    ),
                )
                * 15
            )

        capability_points = 0
        if has_query:
            capability_points = round(top_skill * 45 + min(10, supporting * 8))
            capability_points += role_points + goal_points + legacy_identity_points
        else:
            capability_points = min(25, 10 + len(owned) * 5)
        availability_points = 15 if online else 3
        capacity_points = max(0, 15 - active * 4)
        delivery_points = min(5, completed * 2)
        runtime_points, runtime_reason = _runtime_evidence(
            actor, nodes_by_id, runtimes_by_pair, probe_cutoff
        )
        score = min(
            99,
            max(
                1,
                10
                + capability_points
                + availability_points
                + capacity_points
                + delivery_points
                + runtime_points,
            ),
        )

        matched = [
            skill.name
            for relevance, skill in skill_rows
            if relevance > 0
        ][:3]
        if not matched:
            matched = [skill.name for skill in owned[:3]]

        reasons: list[str] = []
        if matched and has_query:
            reasons.append(f"能力匹配：{'、'.join(matched[:2])}")
        elif matched:
            reasons.append(f"已登记 {len(owned)} 项能力")
        else:
            reasons.append("尚未登记专项技能")
        if role or goal:
            if role:
                reasons.append(
                    f"职责(role){'匹配' if role_points else '未匹配'}：{role}（+{role_points}）"
                    if has_query
                    else f"职责(role)：{role}；输入工作内容后参与匹配（+0）"
                )
            else:
                reasons.append("未声明职责(role)（+0）")
            if goal:
                reasons.append(
                    f"目标(goal){'匹配' if goal_points else '未匹配'}：{goal}（+{goal_points}）"
                    if has_query
                    else f"目标(goal)：{goal}；输入工作内容后参与匹配（+0）"
                )
            else:
                reasons.append("未声明目标(goal)（+0）")
        else:
            reasons.append(
                "未声明职责(role)与目标(goal)（+0）；继续按原有身份字段和技能登记匹配"
            )
        reasons.append(
            "活跃证据：最近 15 分钟在线（+15）"
            if online
            else "活跃证据：最近 15 分钟未见；确认在线后再派单（+3）"
        )
        reasons.append(runtime_reason)
        reasons.append("当前无在手任务" if active == 0 else f"当前 {active} 单在手")
        if completed:
            reasons.append(f"已有 {completed} 单完成记录")

        ranked.append(
            {
                "id": actor.id,
                "display_name": actor.display_name,
                "role": actor.role,
                "goal": actor.goal,
                "runtime": actor.runtime,
                "model": actor.model,
                "node": actor.node,
                "online": online,
                "score": score,
                "matched_skills": matched,
                "reasons": reasons,
                "active_tasks": active,
                "completed_tasks": completed,
            }
        )

    ranked.sort(
        key=lambda row: (
            row["score"],
            row["online"],
            -row["active_tasks"],
            row["completed_tasks"],
            row["id"],
        ),
        reverse=True,
    )
    return ranked[: max(1, min(limit, 20))]
