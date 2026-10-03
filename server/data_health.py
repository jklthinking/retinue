"""Read-only operational freshness checks, separate from historical validity.

Observation age cannot prove that a worker stopped or that old work is invalid.
These checks never sweep leases, alter identities, or remove retained evidence.
Only allow-listed identifiers and timestamps leave this projection.
"""
from __future__ import annotations

import datetime as dt
import json
import os
import re
from collections import defaultdict
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from core.protocol.task import ID_RE

from .db import Actor, Node, RuntimeSession, Task, TokenUsage, utcnow
from .discovery import canonical_runtime, is_sync_actor, model_identity_state

THRESHOLDS = {"node_telemetry": 1800, "runtime_inventory": 86400, "session_sync": 1800, "usage_sync": 1800}
HISTORY_SOURCES_ENV = "RETINUE_SESSION_HISTORY_SOURCES"
_AWARE_ISO = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?(?:Z|[+-]\d{2}:\d{2})$")


def _utc(value: dt.datetime) -> dt.datetime:
    return value.replace(tzinfo=dt.timezone.utc) if value.tzinfo is None else value.astimezone(dt.timezone.utc)


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate-configuration-key")
        result[key] = value
    return result


def _history_sources(now: dt.datetime) -> tuple[dict[tuple[str, str], dt.datetime], bool]:
    """Operator-only classification, all-or-nothing; never echo raw config.

    A cutoff is not permission to remove rows or disable identities. Future
    cutoffs are refused so a classification cannot hide current observations.
    """
    raw = os.environ.get(HISTORY_SOURCES_ENV)
    if raw is None:
        return {}, False
    try:
        if len(raw.encode("utf-8")) > 16384:
            raise ValueError("configuration-too-large")
        values = json.loads(raw, object_pairs_hook=_unique_object)
        if not isinstance(values, list) or len(values) > 100:
            raise ValueError("invalid-configuration-list")
        result: dict[tuple[str, str], dt.datetime] = {}
        for item in values:
            if not isinstance(item, dict) or set(item) != {"actor_id", "runtime", "before"}:
                raise ValueError("invalid-configuration-fields")
            for key in ("actor_id", "runtime"):
                if not isinstance(item[key], str) or len(item[key]) > 64 or not ID_RE.fullmatch(item[key]):
                    raise ValueError("invalid-source-identity")
            before = item["before"]
            if not isinstance(before, str) or not _AWARE_ISO.fullmatch(before):
                raise ValueError("cutoff-requires-aware-iso-time")
            cutoff = _utc(dt.datetime.fromisoformat(before.replace("Z", "+00:00")))
            if cutoff > now:
                raise ValueError("cutoff-cannot-hide-future-observations")
            key = (item["actor_id"], item["runtime"])
            if key in result:
                raise ValueError("duplicate-source-configuration")
            result[key] = cutoff
        return result, False
    except (ValueError, TypeError, OverflowError, UnicodeError, RecursionError):
        return {}, True


def _observation(value: dt.datetime | None, now: dt.datetime, threshold: int) -> dict[str, Any]:
    if value is None:
        return {"state": "unknown", "observed_at": None, "age_seconds": None}
    stamp = _utc(value)
    age = (now - stamp).total_seconds()
    # Clock skew must not turn a future observation into perpetual freshness.
    state = "clock_skew" if age < -300 else "fresh" if age <= threshold else "stale"
    return {"state": state, "observed_at": stamp.isoformat(), "age_seconds": max(0, round(age))}


def _check(key: str, label: str, items: list[dict[str, Any]], total: int, detail: str) -> dict[str, Any]:
    failures = [item for item in items if item["state"] not in {"fresh", "complete"}]
    return {"key": key, "label": label, "observed": total - len(failures), "total": total,
            "status": "attention" if failures else "good" if total else "info", "detail": detail,
            "items": failures[:100], "issue_count": len(failures), "truncated": len(failures) > 100}


def build_data_health(db: Session, *, now: dt.datetime | None = None) -> dict[str, Any]:
    now = _utc(now or utcnow())
    history_sources, invalid_history_config = _history_sources(now)
    actors = list(db.scalars(select(Actor).order_by(Actor.id)))
    by_actor = {actor.id: actor for actor in actors}
    workers = [actor for actor in actors if actor.kind == "agent" and not actor.disabled and not is_sync_actor(actor)]
    nodes = list(db.scalars(select(Node).where(Node.membership_status == "admitted").order_by(Node.id)))
    checks: list[dict[str, Any]] = []
    identity = []
    groups: dict[tuple[str, str, str], list[Actor]] = defaultdict(list)
    for actor in workers:
        complete = bool(actor.node.strip() and actor.runtime.strip() and model_identity_state(actor.model) == "registered")
        identity.append({"kind": "actor", "id": actor.id, "node": actor.node, "runtime": actor.runtime,
                         "state": "complete" if complete else "incomplete"})
        if complete:
            groups[(actor.node.strip().lower(), canonical_runtime(actor.runtime).lower(), actor.model.strip().lower())].append(actor)
    checks.append(_check("worker_identity", "设备与模型登记", identity, len(workers), "仅检查启用的模型 worker；索引服务不计入模型。登记型号仍须运行时证据核验。"))
    duplicate = [{"kind": "actor", "id": actor.id, "node": actor.node, "runtime": actor.runtime, "state": "duplicate_candidate"}
                 for group in groups.values() if len(group) > 1 for actor in group]
    checks.append(_check("worker_duplicates", "重复 worker 候选", duplicate, len(workers), "同设备、运行时、登记型号对应多个身份时需核对用途；不自动合并或撤销身份。"))
    for key, label, attr in [("node_telemetry", "节点上报接收", "updated_at"), ("runtime_inventory", "运行时扫描来源", "runtimes_probed_at")]:
        items = [{"kind": "node", "id": node.id, "node": node.id, **_observation(getattr(node, attr), now, THRESHOLDS[key])} for node in nodes]
        detail = ("最后入库时间可能来自心跳或运行时扫描，只代表收到节点上报，不证明硬件遥测刚采集或模型正在执行。"
                  if key == "node_telemetry" else "按最后运行时扫描入库时间检查来源新鲜度；扫描结果不证明模型正在执行。")
        checks.append(_check(key, label, items, len(nodes), detail))
    open_tasks = list(db.scalars(select(Task).where(Task.archived.is_(False), Task.status.not_in(["done", "cancelled"])).order_by(Task.id)))
    task_owners = []
    leases = []
    for task in open_tasks:
        owner = by_actor.get(task.holder)
        eligible = owner is not None and not owner.disabled and not is_sync_actor(owner)
        task_owners.append({"kind": "task", "id": task.id, "actor_id": task.holder, "state": "complete" if eligible else "owner_unavailable"})
        if task.lease_expires_at is not None:
            stamp = _utc(task.lease_expires_at)
            leases.append({"kind": "task", "id": task.id, "actor_id": task.holder,
                           "state": "fresh" if stamp > now else "expired", "observed_at": stamp.isoformat(),
                           "age_seconds": max(0, round((now - stamp).total_seconds()))})
    checks.append(_check("task_owners", "未结束任务的负责身份", task_owners, len(open_tasks), "停用或索引服务持有的任务需要人工核对接棒；历史任务不改写。"))
    checks.append(_check("task_leases", "未结束任务的执行租约", leases, len(leases), "租约过期仅代表执行授权失效；不推断工作失败，不自动完成、重跑或交棒。"))
    retained_history: list[dict[str, Any]] = []
    for key, label, table, column in [("session_sync", "会话索引同步来源", RuntimeSession, RuntimeSession.synced_at), ("usage_sync", "用量同步来源", TokenUsage, TokenUsage.updated_at)]:
        latest = list(db.execute(select(table.actor_id, table.runtime, func.max(column)).group_by(table.actor_id, table.runtime)))
        sources = []
        for actor_id, runtime, stamp in sorted(latest):
            source = {"kind": "source", "id": actor_id, "actor_id": actor_id, "runtime": runtime,
                      **_observation(stamp, now, THRESHOLDS[key])}
            cutoff = history_sources.get((actor_id, runtime)) if key == "session_sync" else None
            if cutoff is not None and stamp is not None and _utc(stamp) < cutoff:
                retained_history.append({**source, "state": "retained_history", "history_before": cutoff.isoformat()})
            else:
                sources.append(source)
        checks.append(_check(key, label, sources, len(sources), "按同步时间检查已有采集源；会话最后活动时间与历史用量日期不用于判定同步故障。没有该来源不能证明没有工作。"))
    checks.append({"key": "session_history", "label": "历史会话索引来源", "observed": 0,
                   "total": len(retained_history), "retained": len(retained_history), "status": "info",
                   "detail": "管理员明确分类的历史来源仍保留；不计为当前健康通过。相同身份和运行时出现截止时间后的新上报时，自动回到当前同步检查。",
                   "items": retained_history[:100], "issue_count": 0, "truncated": len(retained_history) > 100})
    if invalid_history_config:
        checks.append({"key": "session_history_config", "label": "历史来源配置", "observed": 0, "total": 1,
                       "status": "attention", "detail": "历史来源配置无效；未分类任何来源，所有同步来源继续参与当前检查。",
                       "items": [{"kind": "configuration", "id": "session-history", "state": "invalid_operator_configuration"}],
                       "issue_count": 1, "truncated": False})
    return {"generated_at": now.isoformat(), "mode": "read_only", "thresholds_seconds": dict(THRESHOLDS),
            "history_policy": "历史会话、用量和任务事件保留；新鲜度提示不删除或改写历史，不代替执行验收。", "checks": checks}
