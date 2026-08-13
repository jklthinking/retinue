"""Multi-card pipeline templates, instantiation, and checkpoint resume.

A template is a DAG of card specs. Instantiation creates one card per node
and wires ``depends_on`` through the existing dependency table. Progress
lives on the instance checkpoint so a partial run can continue from the
last created node. Dispatch, claim, and lease stay on M1 / dispatch_v2.
"""

from __future__ import annotations

import json
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from core.protocol.task import ID_RE, PRIORITIES, ProtocolError

from .db import Actor, CardPipelineInstance, CardPipelineTemplate, Squad, Task, utcnow
from .engine import append_annotation_event, create_task
from .guardrails import encode_acceptance


INSTANCE_STATUSES = ("instantiating", "interrupted", "running", "done")


def _require_actor(db: Session, actor_id: str, field: str) -> Actor:
    actor = db.get(Actor, actor_id)
    if actor is None or actor.disabled:
        raise ProtocolError(f"{field}: unknown or disabled actor {actor_id!r}")
    return actor


def _load_checkpoint(instance: CardPipelineInstance) -> dict[str, Any]:
    try:
        payload = json.loads(instance.checkpoint_json or "{}")
    except json.JSONDecodeError:
        payload = {}
    if not isinstance(payload, dict):
        payload = {}
    nodes = payload.get("nodes")
    if not isinstance(nodes, dict):
        nodes = {}
    order = payload.get("order")
    if not isinstance(order, list):
        order = list(nodes)
    return {"nodes": nodes, "order": [str(key) for key in order]}


def _store_checkpoint(instance: CardPipelineInstance, checkpoint: dict[str, Any]) -> None:
    instance.checkpoint_json = json.dumps(checkpoint, ensure_ascii=False, sort_keys=True)
    instance.updated_at = utcnow()


def validate_card_pipeline_spec(
    db: Session, spec: dict[str, Any]
) -> dict[str, Any]:
    """Return a cleaned DAG spec or raise ProtocolError."""
    if not isinstance(spec, dict):
        raise ProtocolError("pipeline spec must be a mapping")
    raw_nodes = spec.get("nodes")
    if not isinstance(raw_nodes, list) or not raw_nodes:
        raise ProtocolError("a card pipeline needs at least one node")
    cleaned: list[dict[str, Any]] = []
    seen: set[str] = set()
    for index, raw in enumerate(raw_nodes):
        if not isinstance(raw, dict):
            raise ProtocolError(f"nodes[{index}] must be a mapping")
        key = str(raw.get("key") or "").strip()
        title = str(raw.get("title") or "").strip()
        if not ID_RE.fullmatch(key):
            raise ProtocolError(f"nodes[{index}].key must be a slug")
        if key in seen:
            raise ProtocolError(f"duplicate node key {key!r}")
        if not title:
            raise ProtocolError(f"nodes[{index}].title must be non-empty")
        seen.add(key)
        holder = str(raw.get("holder") or "").strip() or None
        open_dispatch = bool(raw.get("open_dispatch"))
        if holder:
            _require_actor(db, holder, f"nodes[{index}].holder")
        if open_dispatch and holder:
            raise ProtocolError(
                f"nodes[{index}]: an open-dispatch node cannot also assign an executor"
            )
        squad_id = str(raw.get("squad_id") or "").strip() or None
        if squad_id:
            if db.get(Squad, squad_id) is None:
                raise ProtocolError(f"nodes[{index}].squad_id: unknown squad {squad_id!r}")
            if not open_dispatch:
                raise ProtocolError(
                    f"nodes[{index}]: a squad address requires an open-dispatch node"
                )
        priority = str(raw.get("priority") or "none").strip() or "none"
        if priority not in PRIORITIES:
            raise ProtocolError(
                f"nodes[{index}].priority must be one of: {', '.join(PRIORITIES)}"
            )
        dept = str(raw.get("dept") or "").strip() or None
        if dept and len(dept) > 64:
            raise ProtocolError(f"nodes[{index}].dept must not exceed 64 characters")
        depends_raw = raw.get("depends_on") or []
        if not isinstance(depends_raw, list):
            raise ProtocolError(f"nodes[{index}].depends_on must be a list")
        depends_on = []
        for dep in depends_raw:
            dep_key = str(dep or "").strip()
            if not dep_key:
                continue
            if dep_key == key:
                raise ProtocolError(f"nodes[{index}] cannot depend on itself")
            depends_on.append(dep_key)
        depends_on = list(dict.fromkeys(depends_on))
        acceptance = encode_acceptance(list(raw.get("acceptance") or []))
        cleaned.append(
            {
                "key": key,
                "title": title,
                "holder": holder,
                "open_dispatch": open_dispatch,
                "squad_id": squad_id,
                "dept": dept,
                "priority": priority,
                "acceptance": acceptance,
                "depends_on": depends_on,
            }
        )
    by_key = {node["key"]: node for node in cleaned}
    for node in cleaned:
        for dep in node["depends_on"]:
            if dep not in by_key:
                raise ProtocolError(
                    f"node {node['key']!r} depends on unknown node {dep!r}"
                )
    order = topological_order(cleaned)
    return {"nodes": cleaned, "order": order}


def topological_order(nodes: list[dict[str, Any]]) -> list[str]:
    """Kahn order; raises if the graph has a cycle."""
    remaining = {node["key"]: set(node.get("depends_on") or []) for node in nodes}
    ready = [key for key, deps in remaining.items() if not deps]
    ready.sort()
    ordered: list[str] = []
    while ready:
        key = ready.pop(0)
        ordered.append(key)
        for other, deps in remaining.items():
            if key in deps:
                deps.remove(key)
                if not deps and other not in ordered and other not in ready:
                    ready.append(other)
                    ready.sort()
    if len(ordered) != len(remaining):
        cyclic = sorted(set(remaining) - set(ordered))
        raise ProtocolError("card pipeline has a cycle involving: " + ", ".join(cyclic))
    return ordered


def upsert_card_pipeline_template(
    db: Session,
    *,
    name: str,
    spec: dict[str, Any],
    created_by: str,
) -> CardPipelineTemplate:
    cleaned_name = name.strip()
    if not cleaned_name or len(cleaned_name) > 128:
        raise ProtocolError("template name must be 1 to 128 characters")
    _require_actor(db, created_by, "created_by")
    cleaned = validate_card_pipeline_spec(db, spec)
    row = db.execute(
        select(CardPipelineTemplate).where(CardPipelineTemplate.name == cleaned_name)
    ).scalar()
    if row is None:
        row = CardPipelineTemplate(name=cleaned_name, created_by=created_by)
        db.add(row)
    row.spec_json = json.dumps(cleaned, ensure_ascii=False, sort_keys=True)
    row.updated_at = utcnow()
    db.flush()
    return row


def template_to_dict(row: CardPipelineTemplate) -> dict[str, Any]:
    spec = json.loads(row.spec_json or "{}")
    return {
        "id": row.id,
        "name": row.name,
        "spec": spec,
        "created_by": row.created_by,
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "updated_at": row.updated_at.isoformat() if row.updated_at else None,
    }


def list_card_pipeline_templates(db: Session) -> list[dict[str, Any]]:
    rows = db.execute(
        select(CardPipelineTemplate).order_by(CardPipelineTemplate.name)
    ).scalars()
    return [template_to_dict(row) for row in rows]


def _empty_checkpoint(spec: dict[str, Any]) -> dict[str, Any]:
    order = list(spec.get("order") or [])
    nodes = {
        node["key"]: {"task_id": None, "status": "pending"}
        for node in spec.get("nodes") or []
    }
    return {"nodes": nodes, "order": order}


def _materialize_nodes(
    db: Session,
    instance: CardPipelineInstance,
    spec: dict[str, Any],
    *,
    created_by: str,
    stop_after: str | None = None,
) -> dict[str, Any]:
    checkpoint = _load_checkpoint(instance)
    if not checkpoint["nodes"]:
        checkpoint = _empty_checkpoint(spec)
    by_key = {node["key"]: node for node in spec.get("nodes") or []}
    order = list(spec.get("order") or topological_order(list(by_key.values())))
    checkpoint["order"] = order
    created_stop = False
    for key in order:
        node = by_key[key]
        slot = checkpoint["nodes"].setdefault(key, {"task_id": None, "status": "pending"})
        if slot.get("task_id"):
            if stop_after and key == stop_after:
                created_stop = True
            continue
        if created_stop:
            break
        missing = [
            dep
            for dep in node.get("depends_on") or []
            if not (checkpoint["nodes"].get(dep) or {}).get("task_id")
        ]
        if missing:
            raise ProtocolError(
                f"cannot instantiate {key!r}; missing prerequisite cards: "
                + ", ".join(missing)
            )
        depends_on = [
            checkpoint["nodes"][dep]["task_id"]
            for dep in node.get("depends_on") or []
        ]
        open_dispatch = bool(node.get("open_dispatch"))
        holder = node.get("holder") or created_by
        task = create_task(
            db,
            title=node["title"],
            created_by=created_by,
            holder=holder,
            dept=node.get("dept"),
            priority=node.get("priority") or "none",
            acceptance=node.get("acceptance") or (),
            refs=(f"card-pipeline:{instance.id}:{key}",),
            depends_on=depends_on,
            note=f"instantiated from card pipeline {instance.template_id} node {key}",
            open_dispatch=open_dispatch,
            squad_id=node.get("squad_id"),
        )
        slot["task_id"] = task.id
        slot["status"] = task.status
        checkpoint["nodes"][key] = slot
        _store_checkpoint(instance, checkpoint)
        db.flush()
        if stop_after and key == stop_after:
            created_stop = True
    _store_checkpoint(instance, checkpoint)
    db.flush()
    return checkpoint


def _refresh_checkpoint(
    db: Session, instance: CardPipelineInstance
) -> dict[str, Any]:
    checkpoint = _load_checkpoint(instance)
    all_created = True
    all_done = True
    for key, slot in checkpoint["nodes"].items():
        task_id = slot.get("task_id")
        if not task_id:
            all_created = False
            all_done = False
            slot["status"] = "pending"
            continue
        task = db.get(Task, task_id)
        if task is None:
            slot["status"] = "missing"
            all_created = False
            all_done = False
            continue
        slot["status"] = task.status
        if task.status != "done":
            all_done = False
        checkpoint["nodes"][key] = slot
    if all_done and checkpoint["nodes"]:
        instance.status = "done"
    elif all_created:
        instance.status = "running"
    elif instance.status not in ("interrupted", "instantiating"):
        instance.status = "interrupted"
    _store_checkpoint(instance, checkpoint)
    return checkpoint


def instance_to_dict(
    db: Session,
    instance: CardPipelineInstance,
    template: CardPipelineTemplate | None = None,
) -> dict[str, Any]:
    if template is None:
        template = db.get(CardPipelineTemplate, instance.template_id)
    spec = json.loads(template.spec_json) if template else {}
    by_key = {node["key"]: node for node in spec.get("nodes") or []}
    checkpoint = _load_checkpoint(instance)
    nodes: list[dict[str, Any]] = []
    done = 0
    for key in checkpoint.get("order") or list(checkpoint.get("nodes") or []):
        slot = checkpoint["nodes"].get(key) or {}
        status = slot.get("status") or "pending"
        if status == "done":
            done += 1
        spec_node = by_key.get(key) or {}
        nodes.append(
            {
                "key": key,
                "title": spec_node.get("title"),
                "task_id": slot.get("task_id"),
                "status": status,
                "depends_on": list(spec_node.get("depends_on") or []),
            }
        )
    cursor = next(
        (row["key"] for row in nodes if not row["task_id"]),
        None,
    )
    if cursor is None:
        cursor = next(
            (row["key"] for row in nodes if row["status"] != "done"),
            None,
        )
    total = len(nodes)
    return {
        "id": instance.id,
        "template_id": instance.template_id,
        "template_name": template.name if template else None,
        "instance_key": instance.instance_key,
        "created_by": instance.created_by,
        "status": instance.status,
        "cursor": cursor,
        "progress": {"done": done, "total": total},
        "nodes": nodes,
        "created_at": instance.created_at.isoformat() if instance.created_at else None,
        "updated_at": instance.updated_at.isoformat() if instance.updated_at else None,
    }


def instantiate_card_pipeline(
    db: Session,
    template: CardPipelineTemplate,
    *,
    created_by: str,
    instance_key: str | None = None,
    stop_after: str | None = None,
) -> CardPipelineInstance:
    """Create cards from a template. ``stop_after`` is a test/resume seam."""
    _require_actor(db, created_by, "created_by")
    key = (instance_key or "").strip() or None
    if key and len(key) > 128:
        raise ProtocolError("instance_key must be at most 128 characters")
    if key:
        existing = db.execute(
            select(CardPipelineInstance).where(
                CardPipelineInstance.created_by == created_by,
                CardPipelineInstance.instance_key == key,
            )
        ).scalar()
        if existing is not None:
            _refresh_checkpoint(db, existing)
            return existing
    spec = validate_card_pipeline_spec(db, json.loads(template.spec_json or "{}"))
    instance = CardPipelineInstance(
        template_id=template.id,
        instance_key=key,
        created_by=created_by,
        status="instantiating",
        checkpoint_json=json.dumps(_empty_checkpoint(spec), ensure_ascii=False),
    )
    db.add(instance)
    db.flush()
    try:
        _materialize_nodes(
            db, instance, spec, created_by=created_by, stop_after=stop_after
        )
    except ProtocolError:
        instance.status = "interrupted"
        instance.updated_at = utcnow()
        db.flush()
        raise
    checkpoint = _load_checkpoint(instance)
    pending = [
        node_key
        for node_key, slot in checkpoint["nodes"].items()
        if not slot.get("task_id")
    ]
    instance.status = "interrupted" if pending else "running"
    _refresh_checkpoint(db, instance)
    return instance


def resume_instance(
    db: Session,
    instance: CardPipelineInstance,
    *,
    who: str,
) -> dict[str, Any]:
    """Create any cards still missing, then refresh the checkpoint."""
    template = db.get(CardPipelineTemplate, instance.template_id)
    if template is None:
        raise ProtocolError(f"pipeline template not found: {instance.template_id}")
    spec = validate_card_pipeline_spec(db, json.loads(template.spec_json or "{}"))
    creator = instance.created_by or who
    if instance.status != "done":
        instance.status = "instantiating"
        _materialize_nodes(db, instance, spec, created_by=creator)
    _refresh_checkpoint(db, instance)
    return instance_to_dict(db, instance, template)


def get_instance_status(db: Session, instance: CardPipelineInstance) -> dict[str, Any]:
    _refresh_checkpoint(db, instance)
    return instance_to_dict(db, instance)


def resume_open_instances(
    db: Session, *, now=None
) -> list[dict[str, Any]]:
    """Sweep interrupted instantiations; called from the dispatch reclaim path."""
    del now
    rows = list(
        db.execute(
            select(CardPipelineInstance).where(
                CardPipelineInstance.status.in_(("instantiating", "interrupted"))
            )
        ).scalars()
    )
    results: list[dict[str, Any]] = []
    for row in rows:
        try:
            projected = resume_instance(db, row, who=row.created_by)
        except ProtocolError:
            continue
        results.append(
            {
                "instance_id": row.id,
                "action": "resume",
                "status": projected["status"],
                "cursor": projected["cursor"],
            }
        )
    return results


def record_guardrail_event(
    db: Session,
    task: Task,
    *,
    who: str,
    verdict: dict[str, Any],
) -> None:
    """Leave a short, non-state receipt when structured checks pass."""
    if not verdict.get("checks"):
        return
    names = [str(row.get("check")) for row in verdict.get("checks") or []]
    append_annotation_event(
        db,
        task,
        who=who,
        did="guardrail passed: " + ", ".join(names),
        event_type="guardrail",
        event_key=f"guardrail-pass-{task.id}-{len(task.events) + 1}",
        payload={
            "passed": True,
            "checks": [
                {"check": row.get("check"), "ok": True}
                for row in verdict.get("checks") or []
            ],
        },
    )
