"""Unified Node A + Node B dashboard API.

The router reads redacted observer snapshots and exposes only allow-listed
management actions. It never proxies arbitrary commands or note/session bodies.
"""
from __future__ import annotations

import asyncio
import base64
import difflib
import json
import os
import re
import shutil
import subprocess
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel


router = APIRouter(prefix="/api/kingdom", tags=["kingdom"])
_KINGDOM_ROOT_VALUE = os.environ.get("RETINUE_KINGDOM_ROOT")
HERMES_ROOT = Path(_KINGDOM_ROOT_VALUE) if _KINGDOM_ROOT_VALUE else None
_CASTLE_ROOT_VALUE = os.environ.get("RETINUE_KINGDOM_CASTLE_ROOT")
CASTLE_ROOT = Path(_CASTLE_ROOT_VALUE) if _CASTLE_ROOT_VALUE else HERMES_ROOT
DATA_DIR = HERMES_ROOT / "dashboard-data" if HERMES_ROOT else None
ACTION_SCRIPT = HERMES_ROOT / "scripts" / "kingdom_action.py" if HERMES_ROOT else None
CASTLE_ACTION_SCRIPT = CASTLE_ROOT / "scripts" / "kingdom_action.py" if CASTLE_ROOT else None
OBSERVER_SCRIPT = HERMES_ROOT / "scripts" / "kingdom_observer.py" if HERMES_ROOT else None
CASTLE_HOST = os.environ.get("RETINUE_KINGDOM_CASTLE_HOST")
NODES = ("node-a", "node-b")
# The operations observer runs every 15 minutes. Allow one missed cycle;
# an old process-health snapshot must never stand in for current liveness.
STALE_AFTER_SECONDS = 30 * 60


class KingdomActionRequest(BaseModel):
    node: str
    action: str
    confirm: bool = False
    profile: str = "default"
    target_id: str = ""
    title: str = ""
    body: str = ""
    assignee: str = ""
    priority: int = 0
    goal: bool = False


class KingdomRefreshRequest(BaseModel):
    confirm: bool = False


def _model_dict(value: BaseModel) -> dict[str, Any]:
    method = getattr(value, "model_dump", None)
    return method() if method else value.dict()


def _parse_timestamp(value: Any) -> float:
    if isinstance(value, (int, float)):
        raw = float(value)
        return raw / 1000 if raw > 10_000_000_000 else raw
    if isinstance(value, str) and value:
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
        except ValueError:
            return 0.0
    return 0.0


def _read_snapshot(node: str) -> Optional[dict[str, Any]]:
    if DATA_DIR is None:
        return None
    path = DATA_DIR / f"{node}-overview.json"
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict) or payload.get("schema_version") != 1:
        return None
    return payload


def _merge_skill(target: dict[str, Any], item: dict[str, Any], node: str) -> None:
    target["enabled"] = bool(target.get("enabled") or item.get("enabled"))
    target["nodes"] = sorted(set(target.get("nodes", [])) | {node})
    target["visible_to"] = sorted(set(target.get("visible_to", [])) | set(item.get("visible_to", [])))
    target["owned_by"] = sorted(set(target.get("owned_by", [])) | set(item.get("owned_by", [])))
    sources = set(target.get("sources", []))
    sources.add(str(item.get("source") or "bundled"))
    target["sources"] = sorted(sources)
    if not target.get("description") and item.get("description"):
        target["description"] = item["description"]
    if target.get("category") in {None, "", "uncategorized"} and item.get("category"):
        target["category"] = item["category"]


def _observed_agent(item: dict[str, Any], snapshot: dict[str, Any], stale: bool, now: datetime) -> dict[str, Any]:
    """Preserve snapshot values and distinguish them from current observations."""
    agent = dict(item)
    observed_at = snapshot.get("generated_at")
    usage = dict(item.get("usage") or {})
    daily = usage.get("daily") or []
    dates = [row.get("day") for row in daily if isinstance(row, dict) and isinstance(row.get("day"), str)]
    stat_date = max(dates) if dates else None
    # The legacy observer returns empty daily data when state.db cannot be
    # read. It also coalesces NULL counters to zero, so successful reading is
    # not proof that every provider reported usage.
    available = bool(dates) and all(isinstance(usage.get(key), int) and not isinstance(usage.get(key), bool)
                                    and usage[key] >= 0 for key in ("today_input", "today_output", "today_tokens"))
    window_current = stat_date == now.astimezone().date().isoformat()
    usage.update({
        "available": available, "observed_at": observed_at, "stat_date": stat_date,
        "snapshot_stale": stale, "window_current": window_current,
        "timezone": snapshot.get("timezone") or "observer-local-unrecorded",
        "calendar_match_basis": "snapshot date compared with current service-local date; source timezone unrecorded; no re-bucketing",
        "time_basis": "session_started_day", "completeness": "unknown",
        "accounting": "session cumulative input/output allocated to session start date; legacy nulls coalesced to zero",
    })
    for field in ("today_input", "today_output", "today_tokens", "today_sessions", "week_tokens"):
        usage["recorded_" + field] = usage.get(field)
        usage["current_" + field] = usage.get(field) if available and window_current and not stale else None
    gateway = dict(item.get("gateway") or {})
    gateway.update({"status_basis": "process_health", "observed_at": observed_at, "snapshot_stale": stale,
                    "current_process_healthy": bool(gateway["healthy"]) if not stale and "healthy" in gateway else None})
    agent.update({"usage": usage, "gateway": gateway, "observed_at": observed_at, "snapshot_stale": stale})
    return agent


def _combined_overview() -> dict[str, Any]:
    now = datetime.now(timezone.utc)
    snapshots = {node: _read_snapshot(node) for node in NODES}
    node_rows: list[dict[str, Any]] = []
    agents: list[dict[str, Any]] = []
    crons: list[dict[str, Any]] = []
    task_items: list[dict[str, Any]] = []
    recent_sessions: list[dict[str, Any]] = []
    services: list[dict[str, Any]] = []
    alerts: list[dict[str, Any]] = []
    knowledge_sources: list[dict[str, Any]] = []
    vault_replicas: list[dict[str, Any]] = []
    skill_union: dict[str, dict[str, Any]] = {}
    status_counts: Counter[str] = Counter()

    for node in NODES:
        snapshot = snapshots[node]
        if snapshot is None:
            alerts.append({
                "id": f"{node}:snapshot-missing",
                "node": node,
                "severity": "critical",
                "title": f"{node} 快照不可用",
                "detail": (
                    "RETINUE_KINGDOM_ROOT is not configured."
                    if DATA_DIR is None
                    else "观测器尚未生成可读取的数据快照。"
                ),
            })
            continue
        generated = _parse_timestamp(snapshot.get("generated_at"))
        age = max(0, int(now.timestamp() - generated)) if generated else 10**9
        clock_skew = bool(generated and generated > now.timestamp() + 60)
        stale = age > STALE_AFTER_SECONDS or clock_skew
        node_info = dict(snapshot.get("node") or {})
        node_info.update({
            "freshness_seconds": age,
            "stale": stale,
            "clock_skew": clock_skew,
            "stale_after_seconds": STALE_AFTER_SECONDS,
            "generated_at": snapshot.get("generated_at"),
            "observed_at": snapshot.get("generated_at"),
            "timezone": snapshot.get("timezone") or "observer-local-unrecorded",
            "totals": snapshot.get("totals") or {},
            "vault": snapshot.get("vault") or {"available": False},
            "knowledge": snapshot.get("knowledge") or {},
        })
        node_rows.append(node_info)
        if stale:
            alerts.append({
                "id": f"{node}:snapshot-stale",
                "node": node,
                "severity": "warning",
                "title": f"{node_info.get('label', node)} 快照过期",
                "detail": "观测时间晚于服务时钟，当前状态无法核实。" if clock_skew else f"观测数据已经 {age // 60} 分钟没有更新。",
            })
        agents.extend(_observed_agent(item, snapshot, stale, now) for item in (snapshot.get("agents") or []))
        crons.extend(snapshot.get("crons") or [])
        recent_sessions.extend((snapshot.get("sessions") or {}).get("recent") or [])
        services.extend(snapshot.get("services") or [])
        alerts.extend(snapshot.get("alerts") or [])
        vault = dict(snapshot.get("vault") or {"available": False})
        vault["node"] = node
        vault_replicas.append(vault)
        knowledge_sources.append({
            "node": node,
            **(snapshot.get("knowledge") or {}),
        })
        tasks = snapshot.get("tasks") or {}
        task_items.extend(tasks.get("items") or [])
        status_counts.update({str(k): int(v) for k, v in (tasks.get("by_status") or {}).items()})
        for item in snapshot.get("skills") or []:
            name = str(item.get("name") or "").strip()
            if not name:
                continue
            target = skill_union.setdefault(name, {
                "name": name,
                "description": str(item.get("description") or ""),
                "category": str(item.get("category") or "uncategorized"),
                "enabled": False,
                "nodes": [],
                "visible_to": [],
                "owned_by": [],
                "sources": [],
            })
            _merge_skill(target, item, node)

    recent_sessions.sort(
        key=lambda row: _parse_timestamp(row.get("ended_at") or row.get("started_at")),
        reverse=True,
    )
    task_items.sort(
        key=lambda row: (
            row.get("status") in {"done", "archived"},
            int(row.get("priority") or 0),
            -_parse_timestamp(row.get("created_at")),
        )
    )
    severity_order = {"critical": 0, "warning": 1, "info": 2}
    alerts.sort(key=lambda row: (severity_order.get(str(row.get("severity")), 9), str(row.get("node")), str(row.get("title"))))

    available_vaults = [row for row in vault_replicas if row.get("available")]
    if available_vaults:
        representative = max(available_vaults, key=lambda row: int(row.get("active_notes") or 0))
        cluster_vault = dict(representative)
        sync_peers = next((list(row.get("sync_peers") or []) for row in available_vaults if row.get("sync_peers")), [])
        admission = next((dict(row.get("admission") or {}) for row in available_vaults if (row.get("admission") or {}).get("available")), {"available": False})
        cluster_vault.update({
            "node": None,
            "kind": "peer_cluster",
            "path_label": "SharedKnowledge Obsidian Vault",
            "peer_count": len(sync_peers) or 4,
            "connected_peers": sum(1 for peer in sync_peers if peer.get("connected")),
            "active_notes": max(int(row.get("active_notes") or 0) for row in available_vaults),
            "active_files": max(int(row.get("active_files") or 0) for row in available_vaults),
            "active_size_bytes": max(int(row.get("active_size_bytes") or 0) for row in available_vaults),
            "versions": max(int(row.get("versions") or 0) for row in available_vaults),
            "version_files": max(int(row.get("version_files") or 0) for row in available_vaults),
            "conflicts": max(int(row.get("conflicts") or 0) for row in available_vaults),
            "git_dirty": max(int(row.get("git_dirty") or 0) for row in available_vaults),
            "sync_warnings_24h": sum(int(row.get("sync_warnings_24h") or 0) for row in available_vaults),
            "sync_failures_24h": sum(int(row.get("sync_failures_24h") or 0) for row in available_vaults),
            "latest_mtime": max((str(row.get("latest_mtime") or "") for row in available_vaults), default="") or None,
            "sync_peers": sync_peers,
            "admission": admission,
        })
    else:
        cluster_vault = {"available": False, "kind": "peer_cluster", "peer_count": 4, "connected_peers": 0, "sync_peers": [], "admission": {"available": False}}
    totals = {
        "nodes": len(node_rows),
        "agents": len(agents),
        "active_gateways": sum(1 for row in agents if (row.get("gateway") or {}).get("healthy")),
        "skills": len(skill_union),
        "crons": len(crons),
        "sessions": sum(int((snap.get("sessions") or {}).get("total") or 0) for snap in snapshots.values() if snap),
        "messages": sum(int((snap.get("sessions") or {}).get("messages") or 0) for snap in snapshots.values() if snap),
        "tasks": len(task_items),
        "vault_notes": int(cluster_vault.get("active_notes") or 0),
        "alerts": len(alerts),
    }
    return {
        "schema_version": 1,
        "generated_at": now.isoformat(),
        "nodes": node_rows,
        "agents": agents,
        "skills": sorted(skill_union.values(), key=lambda row: (str(row.get("category")), str(row.get("name")))),
        "sessions": {
            "total": totals["sessions"],
            "messages": totals["messages"],
            "recent": recent_sessions[:40],
        },
        "crons": crons,
        "tasks": {
            "total": len(task_items),
            "by_status": dict(status_counts),
            "items": task_items,
        },
        "vault": cluster_vault,
        "vault_replicas": vault_replicas,
        "knowledge_sources": knowledge_sources,
        "services": services,
        "alerts": alerts,
        "totals": totals,
    }


def _operations_overview(overview: dict[str, Any]) -> dict[str, Any]:
    """Return the small, profile-agnostic operations dashboard payload."""
    agents: list[dict[str, Any]] = []
    total_input = total_output = today_sessions = active_today = 0
    for item in overview["agents"]:
        usage = dict(item.get("usage") or {})
        total_input += int(usage.get("today_input") or 0)
        total_output += int(usage.get("today_output") or 0)
        today_sessions += int(usage.get("today_sessions") or 0)
        if int(usage.get("today_sessions") or 0) > 0:
            active_today += 1
        agents.append({
            "id": item.get("id"),
            "node": item.get("node"),
            "display_name": item.get("display_name"),
            "role": item.get("role"),
            "kind": item.get("kind"),
            "model": item.get("model"),
            "provider": item.get("provider"),
            "gateway": item.get("gateway") or {},
            "sessions": int(item.get("sessions") or 0),
            "messages": int(item.get("messages") or 0),
            "usage": usage,
            "observed_at": item.get("observed_at"),
            "snapshot_stale": item.get("snapshot_stale", True),
        })
    agents.sort(
        key=lambda row: (
            -int((row.get("usage") or {}).get("today_tokens") or 0),
            str(row.get("display_name") or ""),
        )
    )
    open_statuses = {
        str(status): int(count)
        for status, count in (overview["tasks"].get("by_status") or {}).items()
        if str(status) not in {"done", "archived", "cancelled", "failed"}
    }
    available_agents = sum(1 for row in agents if row["usage"].get("available"))
    current_agents = [row for row in agents if row["usage"].get("current_today_tokens") is not None]
    current_complete = bool(agents) and len(current_agents) == len(agents) and len(overview["nodes"]) == len(NODES)
    current_gateways = [row for row in agents if row["gateway"].get("current_process_healthy") is not None]
    observed_dates = sorted({row["usage"]["stat_date"] for row in agents if row["usage"].get("stat_date")})
    return {
        "schema_version": overview["schema_version"],
        "generated_at": overview["generated_at"],
        "source": "hermes_session_snapshots",
        "scope": "Hermes profiles only; separate from Retinue worker daily reports",
        "generated_at_basis": "API projection time; see nodes/agents observed_at for source observation",
        "timezone": "observer-local-unrecorded",
        "stat_dates": observed_dates,
        "time_basis": "session_started_day",
        "stale_after_seconds": STALE_AFTER_SECONDS,
        "usage_available": available_agents > 0,
        "coverage": {"expected_nodes": len(NODES), "available_nodes": len(overview["nodes"]),
                     "available_agents": available_agents, "missing_agents": len(agents) - available_agents,
                     "current_complete": current_complete, "underlying_usage_complete": None},
        "agents": agents,
        "nodes": overview["nodes"],
        "totals": {
            "agents": len(agents),
            "online_agents": sum(1 for row in agents if (row.get("gateway") or {}).get("healthy")),
            "process_healthy_gateways": sum(1 for row in agents if row["gateway"].get("healthy")),
            "current_process_healthy_gateways": sum(1 for row in current_gateways if row["gateway"]["current_process_healthy"])
                if len(current_gateways) == len(agents) and agents and len(overview["nodes"]) == len(NODES) else None,
            "active_today": active_today,
            "today_input": total_input,
            "today_output": total_output,
            "today_tokens": total_input + total_output,
            "today_sessions": today_sessions,
            "current_today_input": sum(row["usage"]["current_today_input"] for row in current_agents) if current_complete else None,
            "current_today_output": sum(row["usage"]["current_today_output"] for row in current_agents) if current_complete else None,
            "current_today_tokens": sum(row["usage"]["current_today_tokens"] for row in current_agents) if current_complete else None,
            "current_today_sessions": sum(row["usage"].get("current_today_sessions") or 0 for row in current_agents) if current_complete else None,
            "recorded_today_input": total_input,
            "recorded_today_output": total_output,
            "recorded_today_tokens": total_input + total_output,
            "available_agents": available_agents,
            "open_tasks": sum(open_statuses.values()),
            "all_tasks": int(overview["tasks"].get("total") or 0),
        },
    }


_OVERVIEW_VIEWS = {"overview", "agents", "tasks", "skills", "infrastructure"}


def _project_overview(overview: dict[str, Any], view: str) -> dict[str, Any]:
    """Return only the data a route actually renders."""
    payload = {
        "schema_version": overview["schema_version"],
        "generated_at": overview["generated_at"],
        "nodes": overview["nodes"],
        "agents": [],
        "skills": [],
        "sessions": {"total": overview["sessions"]["total"], "messages": overview["sessions"]["messages"], "recent": []},
        "crons": [],
        "tasks": {"total": overview["tasks"]["total"], "by_status": overview["tasks"]["by_status"], "items": []},
        "vault": {"available": False},
        "vault_replicas": [],
        "knowledge_sources": [],
        "services": [],
        "alerts": [],
        "totals": overview["totals"],
    }
    if view == "overview":
        payload.update({
            "agents": overview["agents"],
            "sessions": {**overview["sessions"], "recent": overview["sessions"]["recent"][:8]},
            "tasks": {**overview["tasks"], "items": overview["tasks"]["items"][:8]},
            "vault": overview["vault"],
            "alerts": overview["alerts"],
        })
    elif view == "agents":
        payload["agents"] = overview["agents"]
    elif view == "tasks":
        payload.update({"agents": overview["agents"], "crons": overview["crons"], "tasks": overview["tasks"]})
    elif view == "skills":
        payload["skills"] = overview["skills"]
    elif view == "infrastructure":
        payload.update({"services": overview["services"], "alerts": overview["alerts"]})
    return payload

def _encode_action(payload: dict[str, Any]) -> str:
    raw = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _run_action(payload: dict[str, Any]) -> dict[str, Any]:
    node = str(payload.get("node") or "")
    if node not in NODES:
        return {"ok": False, "error": "invalid node"}
    if ACTION_SCRIPT is None:
        return {"ok": False, "error": "RETINUE_KINGDOM_ROOT is not configured"}
    if node == "node-b" and not CASTLE_HOST:
        return {
            "ok": False,
            "error": "RETINUE_KINGDOM_CASTLE_HOST is not configured",
        }
    encoded = _encode_action(payload)
    if node == "node-a":
        cmd = ["/usr/bin/python3", str(ACTION_SCRIPT), encoded]
    else:
        cmd = [
            "ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=10",
            CASTLE_HOST, "/usr/bin/python3", str(CASTLE_ACTION_SCRIPT), encoded,
        ]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=200, check=False)
    except Exception as exc:
        if node == "node-b":
            reason = "remote action timed out" if isinstance(exc, subprocess.TimeoutExpired) else "remote action invocation failed"
            return {"ok": False, "error": reason, "node": node}
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
    if node == "node-b" and proc.returncode != 0:
        return {
            "ok": False,
            "error": "remote action failed",
            "returncode": proc.returncode,
            "node": node,
        }
    text = (proc.stdout or proc.stderr or "").strip()
    try:
        result = json.loads(text.splitlines()[-1]) if text else {}
    except json.JSONDecodeError:
        if node == "node-b":
            result = {"ok": False, "error": "remote action returned an invalid response"}
        else:
            result = {"ok": False, "error": text[-800:] or f"action exited {proc.returncode}"}
    result.setdefault("ok", proc.returncode == 0)
    result["node"] = node
    return result


def _read_audit(node: str) -> dict[str, Any]:
    if HERMES_ROOT is None:
        return {
            "ok": False,
            "items": [],
            "reason": "kingdom root is not configured",
        }
    audit_log = HERMES_ROOT / "logs" / "kingdom-actions.jsonl"
    if node == "node-a":
        try:
            lines = audit_log.read_text(encoding="utf-8").splitlines()[-100:]
        except OSError:
            lines = []
    else:
        if not CASTLE_HOST:
            return {
                "ok": False,
                "items": [],
                "reason": "remote host is not configured",
            }
        remote_audit_log = CASTLE_ROOT / "logs" / "kingdom-actions.jsonl"
        try:
            proc = subprocess.run(
                ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=10", CASTLE_HOST,
                 "tail", "-n", "100", str(remote_audit_log)],
                capture_output=True, text=True, timeout=20, check=False,
            )
        except Exception as exc:
            reason = "remote command timed out" if isinstance(exc, subprocess.TimeoutExpired) else "remote command invocation failed"
            return {"ok": False, "items": [], "reason": reason}
        if proc.returncode != 0:
            return {
                "ok": False,
                "items": [],
                "reason": "remote command failed",
                "returncode": proc.returncode,
            }
        lines = proc.stdout.splitlines()
    rows = []
    for line in lines:
        try:
            row = json.loads(line)
            if isinstance(row, dict):
                row["node"] = node
                rows.append(row)
        except json.JSONDecodeError:
            continue
    return {"ok": True, "items": rows}


@router.get("/overview")
async def kingdom_overview(view: str = "overview") -> dict[str, Any]:
    if view not in _OVERVIEW_VIEWS:
        raise HTTPException(status_code=400, detail="invalid kingdom view")
    overview = await asyncio.to_thread(_combined_overview)
    return _project_overview(overview, view)

@router.get("/operations")
async def kingdom_operations() -> dict[str, Any]:
    """Return global usage and activity without applying a Profile scope."""
    overview = await asyncio.to_thread(_combined_overview)
    return _operations_overview(overview)

@router.get("/knowledge")
async def kingdom_knowledge() -> dict[str, Any]:
    """Return the small knowledge-plane payload used by the slow-tailnet UI."""
    overview = await asyncio.to_thread(_combined_overview)
    return {
        "schema_version": overview["schema_version"],
        "generated_at": overview["generated_at"],
        "vault": overview["vault"],
        "knowledge_sources": overview["knowledge_sources"],
    }


@router.post("/actions")
async def kingdom_action(body: KingdomActionRequest) -> dict[str, Any]:
    payload = _model_dict(body)
    if payload.get("confirm") is not True:
        raise HTTPException(status_code=400, detail="explicit confirmation is required")
    if payload.get("node") not in NODES:
        raise HTTPException(status_code=400, detail="invalid node")
    result = await asyncio.to_thread(_run_action, payload)
    if not result.get("ok"):
        raise HTTPException(status_code=400, detail=result)
    return result


@router.post("/refresh")
async def kingdom_refresh(body: KingdomRefreshRequest) -> dict[str, Any]:
    if body.confirm is not True:
        raise HTTPException(status_code=400, detail="explicit confirmation is required")
    payloads = [
        {"node": node, "action": "snapshot_refresh", "confirm": True}
        for node in NODES
    ]
    results = await asyncio.gather(
        *(asyncio.to_thread(_run_action, payload) for payload in payloads)
    )
    return {"ok": all(row.get("ok") for row in results), "results": results}


# ---------------------------------------------------------------------------
# Vault sync-conflict management (node-a-local vault; edits propagate via
# Syncthing to all peers). This is the ONE sanctioned write path into note
# bodies — everything else on this router stays metadata-only.
# ---------------------------------------------------------------------------

_VAULT_VALUE = os.environ.get("RETINUE_KINGDOM_VAULT")
VAULT_CANDIDATES = (Path(_VAULT_VALUE),) if _VAULT_VALUE else ()
CONFLICT_MARKER = "sync-conflict"
_CONFLICT_NAME_RE = re.compile(r"\.sync-conflict-(\d{8})-(\d{6})-([A-Z0-9]+)", re.IGNORECASE)
CONFLICT_TRASH = HERMES_ROOT / "trash" / "vault-conflicts" if HERMES_ROOT else None
CONFLICT_AUDIT_LOG = HERMES_ROOT / "logs" / "kingdom-actions.jsonl" if HERMES_ROOT else None
_CONFLICT_SKIP_DIRS = {".git", ".stversions"}
_MAX_CONFLICT_TEXT = 1_500_000  # bytes; larger files are view-only summaries


class ConflictResolveRequest(BaseModel):
    path: str
    action: str  # keep_current | keep_conflict | save_edit
    target: str = ""
    content: str = ""
    confirm: bool = False


def _vault_root() -> Optional[Path]:
    return next((p for p in VAULT_CANDIDATES if p.is_dir()), None)


def _safe_vault_path(root: Path, rel: str) -> Path:
    candidate = (root / rel).resolve()
    if not str(candidate).startswith(str(root.resolve()) + os.sep):
        raise HTTPException(status_code=400, detail="path escapes vault root")
    return candidate


def _conflict_meta(root: Path, path: Path) -> dict[str, Any]:
    rel = str(path.relative_to(root))
    match = _CONFLICT_NAME_RE.search(path.name)
    original_name = _CONFLICT_NAME_RE.sub("", path.name)
    sibling = path.parent / original_name
    try:
        stat = path.stat()
        size, mtime = stat.st_size, datetime.fromtimestamp(stat.st_mtime, timezone.utc).isoformat()
    except OSError:
        size, mtime = 0, None
    return {
        "path": rel,
        "name": path.name,
        "original_name": original_name,
        "original_path": str(sibling.relative_to(root)) if sibling.is_file() else None,
        "device": match.group(3) if match else None,
        "conflict_at": f"{match.group(1)}-{match.group(2)}" if match else None,
        "size": size,
        "mtime": mtime,
        "archived": CONFLICT_MARKER not in original_name and "archive" in rel.lower(),
        "editable": path.suffix.lower() in {".md", ".txt", ".canvas", ".json"} and size <= _MAX_CONFLICT_TEXT,
    }


def _list_conflicts() -> dict[str, Any]:
    root = _vault_root()
    if root is None:
        return {"available": False, "items": []}
    items: list[dict[str, Any]] = []
    for current, dirs, files in os.walk(root):
        dirs[:] = [d for d in dirs if d not in _CONFLICT_SKIP_DIRS]
        for name in files:
            if CONFLICT_MARKER in name.lower():
                items.append(_conflict_meta(root, Path(current) / name))
    items.sort(key=lambda row: str(row.get("mtime") or ""), reverse=True)
    return {"available": True, "root": root.name, "items": items}


def _original_candidates(root: Path, conflict: Path) -> list[str]:
    """When the conflict file was moved away from its原位置, guess originals by name."""
    original_name = _CONFLICT_NAME_RE.sub("", conflict.name)
    found: list[str] = []
    for current, dirs, files in os.walk(root):
        dirs[:] = [d for d in dirs if d not in _CONFLICT_SKIP_DIRS]
        if original_name in files:
            candidate = Path(current) / original_name
            if CONFLICT_MARKER not in str(candidate).lower():
                found.append(str(candidate.relative_to(root)))
        if len(found) >= 8:
            break
    return found


def _read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace")


def _conflict_audit(entry: dict[str, Any]) -> None:
    if CONFLICT_AUDIT_LOG is None:
        raise RuntimeError("RETINUE_KINGDOM_ROOT is not configured")
    entry = {"timestamp": datetime.now(timezone.utc).isoformat(), "node": "node-a", **entry}
    try:
        CONFLICT_AUDIT_LOG.parent.mkdir(parents=True, exist_ok=True)
        with CONFLICT_AUDIT_LOG.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except OSError:
        pass


def _archive_conflict(path: Path) -> str:
    """Move a resolved conflict file into the trash (never hard-delete)."""
    if CONFLICT_TRASH is None:
        raise RuntimeError("RETINUE_KINGDOM_ROOT is not configured")
    CONFLICT_TRASH.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    dest = CONFLICT_TRASH / f"{stamp}-{path.name}"
    shutil.move(str(path), str(dest))
    return str(dest)


def _conflict_detail(rel: str) -> dict[str, Any]:
    root = _vault_root()
    if root is None:
        if not VAULT_CANDIDATES:
            raise HTTPException(
                status_code=503,
                detail="RETINUE_KINGDOM_VAULT is not configured",
            )
        raise HTTPException(status_code=404, detail="vault not found on this node")
    path = _safe_vault_path(root, rel)
    if not path.is_file() or CONFLICT_MARKER not in path.name.lower():
        raise HTTPException(status_code=404, detail="conflict file not found")
    meta = _conflict_meta(root, path)
    detail: dict[str, Any] = {"conflict": meta, "original": None, "candidates": [], "diff": []}
    if not meta["editable"]:
        return detail
    conflict_text = _read_text(path)
    detail["conflict"] = {**meta, "content": conflict_text}
    original_rel = meta.get("original_path")
    candidates = [original_rel] if original_rel else _original_candidates(root, path)
    detail["candidates"] = candidates
    if len(candidates) == 1:
        original_path = _safe_vault_path(root, candidates[0])
        if original_path.is_file() and original_path.stat().st_size <= _MAX_CONFLICT_TEXT:
            original_text = _read_text(original_path)
            detail["original"] = {"path": candidates[0], "content": original_text}
            detail["diff"] = list(difflib.unified_diff(
                original_text.splitlines(), conflict_text.splitlines(),
                fromfile=f"当前版本 · {candidates[0]}", tofile=f"冲突版本 · {meta['name']}", lineterm="", n=3,
            ))
    return detail


def _conflict_resolve(body: ConflictResolveRequest) -> dict[str, Any]:
    root = _vault_root()
    if root is None:
        if not VAULT_CANDIDATES:
            raise HTTPException(
                status_code=503,
                detail="RETINUE_KINGDOM_VAULT is not configured",
            )
        raise HTTPException(status_code=404, detail="vault not found on this node")
    if CONFLICT_TRASH is None or CONFLICT_AUDIT_LOG is None:
        raise HTTPException(
            status_code=503,
            detail="RETINUE_KINGDOM_ROOT is not configured",
        )
    path = _safe_vault_path(root, body.path)
    if not path.is_file() or CONFLICT_MARKER not in path.name.lower():
        raise HTTPException(status_code=404, detail="conflict file not found")
    action = body.action
    result: dict[str, Any] = {"ok": True, "action": action, "path": body.path}
    if action == "keep_current":
        result["trashed"] = _archive_conflict(path)
    elif action in {"keep_conflict", "save_edit"}:
        if not body.target:
            raise HTTPException(status_code=400, detail="target path is required")
        target = _safe_vault_path(root, body.target)
        if CONFLICT_MARKER in target.name.lower():
            raise HTTPException(status_code=400, detail="target may not be a conflict file")
        # Back up the current target before overwriting, then adopt the new content.
        if target.is_file():
            CONFLICT_TRASH.mkdir(parents=True, exist_ok=True)
            stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
            backup = CONFLICT_TRASH / f"{stamp}-pre-resolve-{target.name}"
            shutil.copy2(str(target), str(backup))
            result["backup"] = str(backup)
        content = _read_text(path) if action == "keep_conflict" else body.content
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        result["target"] = body.target
        result["trashed"] = _archive_conflict(path)
    else:
        raise HTTPException(status_code=400, detail="invalid action")
    _conflict_audit({"action": f"vault_conflict_{action}", "target_id": body.path, "title": body.target, "ok": True})
    return result


@router.get("/conflicts")
async def kingdom_conflicts() -> dict[str, Any]:
    return await asyncio.to_thread(_list_conflicts)


@router.get("/conflicts/detail")
async def kingdom_conflict_detail(path: str) -> dict[str, Any]:
    return await asyncio.to_thread(_conflict_detail, path)


@router.post("/conflicts/resolve")
async def kingdom_conflict_resolve(body: ConflictResolveRequest) -> dict[str, Any]:
    if body.confirm is not True:
        raise HTTPException(status_code=400, detail="explicit confirmation is required")
    return await asyncio.to_thread(_conflict_resolve, body)


@router.get("/audit")
async def kingdom_audit() -> dict[str, Any]:
    node_a, node_b = await asyncio.gather(
        asyncio.to_thread(_read_audit, "node-a"),
        asyncio.to_thread(_read_audit, "node-b"),
    )
    rows = node_a["items"] + node_b["items"]
    rows.sort(key=lambda row: _parse_timestamp(row.get("timestamp")), reverse=True)
    result = {"items": rows[:120]}
    gaps = []
    for node, read in (("node-a", node_a), ("node-b", node_b)):
        if read["ok"]:
            continue
        gap = {"node": node, "reason": read["reason"]}
        if "returncode" in read:
            gap["returncode"] = read["returncode"]
        gaps.append(gap)
    if gaps:
        result["gaps"] = gaps
    return result
