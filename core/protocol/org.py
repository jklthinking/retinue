"""Organization-model validation and initialization."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from .task import ID_RE, ProtocolError


def validate_org(data: Any) -> None:
    if not isinstance(data, dict):
        raise ProtocolError("org.yaml must be a mapping")
    required = {"org", "departments", "agents", "nodes"}
    missing = sorted(required - data.keys())
    if missing:
        raise ProtocolError(f"missing org fields: {', '.join(missing)}")
    if not isinstance(data["org"], str) or not ID_RE.fullmatch(data["org"]):
        raise ProtocolError("org must be a lowercase hyphenated id")
    for field in ("departments", "agents", "nodes"):
        if not isinstance(data[field], list):
            raise ProtocolError(f"{field} must be a list")

    for field in ("departments", "agents", "nodes"):
        if any(not isinstance(item, dict) for item in data[field]):
            raise ProtocolError(f"every {field} item must be a mapping")

    raw_ids = [
        item.get("id")
        for field in ("departments", "agents", "nodes")
        for item in data[field]
    ]
    all_ids = raw_ids
    if any(not isinstance(value, str) or not ID_RE.fullmatch(value) for value in all_ids):
        raise ProtocolError("every department, agent, and node needs a valid id")
    if len(all_ids) != len(set(all_ids)):
        raise ProtocolError("department, agent, and node ids must be globally unique")

    departments = {item["id"]: item for item in data["departments"]}
    agents = {item["id"]: item for item in data["agents"]}
    nodes = {item["id"]: item for item in data["nodes"]}

    for department in departments.values():
        if not isinstance(department.get("name"), str) or not department["name"].strip():
            raise ProtocolError("every department needs a name")
    for agent_id, agent in agents.items():
        if agent.get("dept") not in departments:
            raise ProtocolError(f"agent {agent_id} references an unknown department")
        if agent.get("node") not in nodes:
            raise ProtocolError(f"agent {agent_id} references an unknown node")
        for field in ("runtime",):
            if not isinstance(agent.get(field), str) or not agent[field].strip():
                raise ProtocolError(f"agent {agent_id} needs {field}")
        hook = agent.get("on_claim")
        if hook is not None and not (
            isinstance(hook, str) and hook.strip()
            or isinstance(hook, list) and hook and all(isinstance(part, str) and part for part in hook)
        ):
            raise ProtocolError(f"agent {agent_id} on_claim must be a command string or argv list")
    for department_id, department in departments.items():
        lead = department.get("lead")
        if lead is not None and (
            lead not in agents or agents[lead].get("dept") != department_id
        ):
            raise ProtocolError(f"lead for {department_id} must be an agent in that department")


def initialize(path: Path | str, org_id: str) -> Path:
    root = Path(path)
    target = root / "org.yaml"
    if target.exists():
        raise ProtocolError(f"refusing to overwrite {target}")
    data = {"org": org_id, "departments": [], "agents": [], "nodes": []}
    validate_org(data)
    root.mkdir(parents=True, exist_ok=True)
    (root / "tasks").mkdir(exist_ok=True)
    (root / "nodes").mkdir(exist_ok=True)
    target.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
    return target
