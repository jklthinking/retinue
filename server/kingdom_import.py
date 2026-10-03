"""Turn observer discoveries into explicit, idempotent roster proposals.

The observer directory is an input only. Scheduled imports publish ordinary
task cards; they never create or update roster entities. An administrator may
then apply a proposal through the task action, which records the automation
that performed the approved change in the existing task-event chain.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import re
from typing import Any, Iterable

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from core.protocol.task import ProtocolError, validate_ledger_text

from .db import Actor, KnowledgeSource, Node, Skill, Task, TaskEvent
from .engine import create_task, update_task
from .membership import admit_node

PROPOSAL_EVENT_TYPE = "roster_proposal"
PROPOSAL_PAYLOAD_KEY = "roster_proposal"
PROPOSAL_VERSION = 1
DEFAULT_PERFORMING_AGENT = "kingdom-import"

# snapshot status -> retinue status
STATUS_MAP = {
    "triage": "queued",
    "todo": "queued",
    "ready": "queued",
    "scheduled": "queued",
    "running": "doing",
    "blocked": "blocked",
    "done": "done",
    "archived": "done",
}

PRIORITY_MAP = {0: "none", 1: "low", 2: "medium", 3: "high", 4: "urgent"}
_KIND_ORDER = {"actor": 0, "node": 1, "skill": 2, "knowledge_source": 3, "task": 4}
_PROPOSAL_FIELDS = {
    "actor": {"id", "kind", "display_name", "runtime", "model", "node"},
    "node": {"id", "label"},
    "skill": {"name", "description", "category", "enabled", "owners"},
    "knowledge_source": {"name", "kind", "docs", "size_bytes", "notes"},
    "task": {"title", "holder", "dept", "priority", "target_status", "source_ref"},
}
_SOURCE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,95}$")


def _slug(value: str) -> str:
    cleaned = "".join(
        c if "a" <= c <= "z" or "0" <= c <= "9" else "-"
        for c in value.lower()
    ).strip("-")
    while "--" in cleaned:
        cleaned = cleaned.replace("--", "-")
    return cleaned or "unknown"


def _digest(*parts: str, length: int = 32) -> str:
    return hashlib.sha256("\0".join(parts).encode()).hexdigest()[:length]


def _entity_key(kind: str, identity: str) -> str:
    return f"roster-{_digest('v1', kind, identity)}"


def _safe_text(value: Any, fallback: str, *, max_length: int = 200) -> str:
    """Keep snapshot-derived card text one-line and outside the ledger refusals."""
    text = " ".join(str(value or "").split()).strip()[:max_length]
    if not text:
        return fallback
    try:
        return validate_ledger_text(text, "snapshot value", max_length=max_length)
    except ProtocolError:
        return fallback


def _task_source(raw_id: Any, item: dict[str, Any]) -> tuple[str, str]:
    raw = str(raw_id or "").strip()
    identity = raw or _digest(
        str(item.get("title") or ""),
        str(item.get("assignee") or ""),
        str(item.get("status") or ""),
    )
    safe_id = raw if _SOURCE_ID.fullmatch(raw) else f"item-{_digest(identity, length=16)}"
    source_ref = f"kingdom:{safe_id}"
    return safe_id, source_ref


def _task_with_ref_exists(db: Session, refs: Iterable[str]) -> bool:
    for ref in dict.fromkeys(refs):
        if db.execute(
            select(Task.id).where(Task.refs_json.like(f'%"{ref}"%'))
        ).first():
            return True
    return False


def _item_exists(db: Session, item: dict[str, Any]) -> bool:
    kind = item["kind"]
    fields = item["fields"]
    if kind == "actor":
        return db.get(Actor, fields["id"]) is not None
    if kind == "node":
        return db.get(Node, fields["id"]) is not None
    if kind == "skill":
        return db.execute(select(Skill.id).where(Skill.name == fields["name"])).first() is not None
    if kind == "knowledge_source":
        return db.execute(
            select(KnowledgeSource.id).where(KnowledgeSource.name == fields["name"])
        ).first() is not None
    if kind == "task":
        return _task_with_ref_exists(db, [fields["source_ref"]])
    raise ProtocolError(f"unsupported roster proposal kind: {kind}")


def _validated_proposal_item(raw_item: Any) -> dict[str, Any]:
    if not isinstance(raw_item, dict):
        raise ProtocolError("roster proposal item must be a mapping")
    kind = raw_item.get("kind")
    identity = raw_item.get("identity")
    key = raw_item.get("key")
    fields = raw_item.get("fields")
    if kind not in _PROPOSAL_FIELDS or raw_item.get("action") != "create":
        raise ProtocolError("roster proposal item has an unsupported action or kind")
    if not isinstance(identity, str) or not identity:
        raise ProtocolError("roster proposal identity must be non-empty")
    validate_ledger_text(identity, "roster proposal identity", max_length=200)
    if key != _entity_key(kind, identity):
        raise ProtocolError("roster proposal item key does not match its identity")
    if not isinstance(fields, dict) or set(fields) != _PROPOSAL_FIELDS[kind]:
        raise ProtocolError("roster proposal item fields do not match its kind")

    for name, value in fields.items():
        if isinstance(value, str):
            validate_ledger_text(value, f"roster proposal {name}", max_length=240)
        elif isinstance(value, list):
            if not all(isinstance(item, str) for item in value):
                raise ProtocolError(f"roster proposal {name} must contain strings")
            for item in value:
                validate_ledger_text(item, f"roster proposal {name}", max_length=64)
        elif not isinstance(value, (int, bool)):
            raise ProtocolError(f"roster proposal {name} has an unsupported value")
    if kind in {"actor", "node"} and fields["id"] != identity:
        raise ProtocolError("roster proposal identity disagrees with entity id")
    if kind in {"skill", "knowledge_source"} and fields["name"] != identity:
        raise ProtocolError("roster proposal identity disagrees with entity name")
    if kind == "actor" and fields["kind"] != "agent":
        raise ProtocolError("observer proposals may create only agent actors")
    if kind == "task":
        if fields["priority"] not in PRIORITY_MAP.values():
            raise ProtocolError("roster proposal task has an invalid priority")
        if fields["target_status"] not in STATUS_MAP.values():
            raise ProtocolError("roster proposal task has an invalid target status")
        if fields["source_ref"] != f"kingdom:{identity}":
            raise ProtocolError("roster proposal task source does not match its identity")
    for field in ("docs", "size_bytes"):
        if field in fields and (
            isinstance(fields[field], bool) or fields[field] < 0
        ):
            raise ProtocolError(f"roster proposal {field} must be a non-negative integer")
    return dict(raw_item)


def _proposal_items(event: TaskEvent, *, strict: bool = True) -> list[dict[str, Any]]:
    try:
        payload = json.loads(event.payload_json or "{}")
    except (TypeError, json.JSONDecodeError):
        return []
    proposal = payload.get(PROPOSAL_PAYLOAD_KEY) if isinstance(payload, dict) else None
    if not isinstance(proposal, dict) or proposal.get("version") != PROPOSAL_VERSION:
        return []
    items = proposal.get("items")
    if not isinstance(items, list):
        return []
    try:
        return [_validated_proposal_item(item) for item in items]
    except ProtocolError:
        if strict:
            raise
        return []


def _already_proposed_keys(db: Session) -> set[str]:
    keys: set[str] = set()
    events = db.execute(
        select(TaskEvent).where(TaskEvent.event_type == PROPOSAL_EVENT_TYPE)
    ).scalars()
    for event in events:
        for item in _proposal_items(event, strict=False):
            key = item.get("key")
            if isinstance(key, str):
                keys.add(key)
    return keys


def _add_discovery(
    discoveries: dict[str, dict[str, Any]],
    entity_kind: str,
    identity: str,
    **fields: Any,
) -> None:
    key = _entity_key(entity_kind, identity)
    discoveries.setdefault(
        key,
        {
            "key": key,
            "kind": entity_kind,
            "identity": identity,
            "action": "create",
            "fields": fields,
        },
    )


def _collect_discoveries(files: list[Path], alias: dict[str, str]) -> dict[str, dict[str, Any]]:
    discoveries: dict[str, dict[str, Any]] = {}
    for path in files:
        data = json.loads(path.read_text(encoding="utf-8"))
        node_raw = data.get("node")
        if isinstance(node_raw, dict):
            node = _slug(str(node_raw.get("id") or node_raw.get("label") or ""))
        else:
            node = _slug(str(node_raw or path.stem.removesuffix("-overview")))

        for agent in data.get("agents", []):
            profile = _slug(str(agent.get("profile") or ""))
            actor_id = _slug(alias.get(profile, profile))
            _add_discovery(
                discoveries,
                "actor",
                actor_id,
                id=actor_id,
                kind="agent",
                display_name=_safe_text(agent.get("display_name"), actor_id, max_length=128),
                runtime=_safe_text(agent.get("provider"), "", max_length=64),
                model=_safe_text(agent.get("model"), "", max_length=64),
                node=node,
            )

        items = data.get("tasks") or {}
        for item in items.get("items", []) if isinstance(items, dict) else []:
            identity, source_ref = _task_source(item.get("id"), item)
            assignee = _slug(
                alias.get(_slug(str(item.get("assignee") or "")))
                or str(item.get("assignee") or "unassigned")
            )
            _add_discovery(
                discoveries,
                "task",
                identity,
                title=_safe_text(item.get("title"), source_ref, max_length=240),
                holder=assignee,
                dept=node,
                priority=PRIORITY_MAP.get(item.get("priority"), "none"),
                target_status=STATUS_MAP.get(str(item.get("status")), "queued"),
                source_ref=source_ref,
            )

        if isinstance(node_raw, dict):
            _add_discovery(
                discoveries,
                "node",
                node,
                id=node,
                label=_safe_text(node_raw.get("label"), node, max_length=128),
            )

        for item in data.get("skills", []):
            name = _safe_text(item.get("name"), "", max_length=128)
            if not name:
                continue
            owners = [
                _slug(str(owner))
                for owner in (item.get("owned_by") or item.get("visible_to") or [])
            ]
            _add_discovery(
                discoveries,
                "skill",
                name,
                name=name,
                description=_safe_text(item.get("description"), "", max_length=240),
                category=_safe_text(item.get("category"), "", max_length=64),
                enabled=bool(item.get("enabled", True)),
                owners=owners,
            )

        vault = data.get("vault")
        if isinstance(vault, dict) and vault.get("available"):
            label = _safe_text(vault.get("path_label"), "Obsidian Vault", max_length=96)
            name = f"{node} · {label}"
            _add_discovery(
                discoveries,
                "knowledge_source",
                name,
                name=name,
                kind="obsidian",
                docs=int(vault.get("active_notes") or 0),
                size_bytes=int(vault.get("active_size_bytes") or 0),
                notes=(
                    f"Files {int(vault.get('active_files') or 0)} · "
                    f"conflicts {int(vault.get('conflicts') or 0)} · "
                    f"sync warnings 24h {int(vault.get('sync_warnings_24h') or 0)}"
                ),
            )

        knowledge_counts = data.get("knowledge")
        if isinstance(knowledge_counts, dict):
            label_map = {
                "shared_notes": ("Shared notes", "notes"),
                "shared_wiki_notes": ("Shared wiki", "wiki"),
                "shared_memory_notes": ("Shared memory", "memory"),
                "raw_mirror_notes": ("Archive mirror", "archive"),
            }
            for raw_key, count in knowledge_counts.items():
                fallback = f"Corpus {_digest(str(raw_key), length=8)}"
                label, source_kind = label_map.get(
                    raw_key,
                    (_safe_text(raw_key, fallback, max_length=64), "corpus"),
                )
                name = f"{node} · {label}"
                _add_discovery(
                    discoveries,
                    "knowledge_source",
                    name,
                    name=name,
                    kind=source_kind,
                    docs=int(count or 0),
                    size_bytes=0,
                    notes="",
                )
    return discoveries


def _criterion(item: dict[str, Any]) -> str:
    fields = item["fields"]
    kind = item["kind"].replace("_", " ")
    identity = _safe_text(item["identity"], item["key"], max_length=96)
    details: list[str] = []
    for field in ("display_name", "runtime", "model", "node", "label", "holder", "priority", "target_status", "category", "kind", "docs"):
        value = fields.get(field)
        if value not in (None, "", []):
            details.append(f"{field}={value}")
    suffix = f" ({', '.join(details)})" if details else ""
    return _safe_text(
        f"Approve create {kind} {identity}{suffix}",
        f"Approve create {kind} {item['key']}",
        max_length=240,
    )


def import_kingdom(
    db: Session,
    snapshot_dir: Path | str,
    *,
    created_by: str,
    alias: dict[str, str] | None = None,
    performed_by: str = DEFAULT_PERFORMING_AGENT,
) -> dict[str, int]:
    """Publish one card for all newly discovered, not-yet-proposed additions."""
    snapshot_dir = Path(snapshot_dir)
    files = sorted(snapshot_dir.glob("*-overview.json"))
    if not files:
        raise FileNotFoundError("no observer snapshots found")
    authority = db.get(Actor, created_by)
    if authority is None or authority.disabled:
        raise ProtocolError(f"created_by: unknown or disabled actor {created_by!r}")

    discoveries = _collect_discoveries(files, alias or {})
    proposed_keys = _already_proposed_keys(db)
    new_items = [
        item
        for key, item in discoveries.items()
        if key not in proposed_keys and not _item_exists(db, item)
    ]
    new_items.sort(key=lambda item: (_KIND_ORDER[item["kind"]], item["key"]))
    skipped = len(discoveries) - len(new_items)
    if not new_items:
        return {"proposals": 0, "proposed": 0, "skipped": skipped}

    batch_key = f"proposal-{_digest(*(item['key'] for item in new_items))}"
    payload = {
        PROPOSAL_PAYLOAD_KEY: {
            "version": PROPOSAL_VERSION,
            "idempotence_key": batch_key,
            "items": new_items,
        }
    }
    refs = [f"kingdom-proposal:{batch_key.removeprefix('proposal-')}"]
    try:
        with db.begin_nested():
            create_task(
                db,
                title=f"Roster proposal: {len(new_items)} additions",
                created_by=created_by,
                holder=created_by,
                dept="roster",
                priority="medium",
                acceptance=[_criterion(item) for item in new_items],
                refs=refs,
                note=f"Automation proposed {len(new_items)} roster additions",
                event_type=PROPOSAL_EVENT_TYPE,
                event_key=batch_key,
                event_payload=payload,
                performing_agent=performed_by,
            )
    except IntegrityError:
        # The unique TaskEvent.event_key index is the cross-process backstop.
        return {"proposals": 0, "proposed": 0, "skipped": len(discoveries)}
    return {"proposals": 1, "proposed": len(new_items), "skipped": skipped}


def proposal_for_task(task: Task) -> dict[str, Any] | None:
    for event in task.events:
        if event.event_type != PROPOSAL_EVENT_TYPE:
            continue
        items = _proposal_items(event)
        if items:
            return {
                "version": PROPOSAL_VERSION,
                "idempotence_key": event.event_key,
                "items": items,
            }
    return None


def _apply_item(
    db: Session,
    item: dict[str, Any],
    *,
    authorising_identity: str,
    performing_agent: str,
    proposal_task_id: str,
) -> bool:
    if _item_exists(db, item):
        return False
    kind = item["kind"]
    fields = dict(item["fields"])
    if kind == "actor":
        db.add(Actor(**fields))
    elif kind == "node":
        _node, changed = admit_node(
            db,
            node_id=fields["id"],
            label=fields["label"],
            admitted_by=authorising_identity,
        )
        return changed
    elif kind == "skill":
        owners = fields.pop("owners", [])
        db.add(
            Skill(
                **fields,
                source="kingdom",
                owners_json=json.dumps(owners),
            )
        )
    elif kind == "knowledge_source":
        db.add(KnowledgeSource(**fields))
    elif kind == "task":
        target = fields.pop("target_status")
        source_ref = fields.pop("source_ref")
        imported = create_task(
            db,
            created_by=authorising_identity,
            acceptance=["Matches the approved observer proposal"],
            refs=[source_ref, f"proposal-task:{proposal_task_id}"],
            note="Automation created an approved imported task",
            event_payload={"source_proposal": {"task_id": proposal_task_id, "item_key": item["key"]}},
            performing_agent=performing_agent,
            **fields,
        )
        if target in ("doing", "blocked", "done"):
            update_task(
                db,
                imported,
                who=authorising_identity,
                is_privileged=True,
                status="doing",
                note="Automation synchronized the approved task as started",
                performing_agent=performing_agent,
            )
        if target == "blocked":
            update_task(
                db,
                imported,
                who=authorising_identity,
                is_privileged=True,
                status="blocked",
                blocked_reason="The approved source item was marked blocked",
                note="Automation synchronized the approved blocked state",
                performing_agent=performing_agent,
            )
        elif target == "done":
            update_task(
                db,
                imported,
                who=authorising_identity,
                is_privileged=True,
                status="done",
                note="Automation synchronized the approved completed state",
                performing_agent=performing_agent,
            )
        return True
    else:
        raise ProtocolError(f"unsupported roster proposal kind: {kind}")
    db.flush()
    return True


def apply_kingdom_proposal(
    db: Session,
    task: Task,
    *,
    authorised_by: str,
    performed_by: str = "retinue-server",
) -> dict[str, int]:
    """Approve and atomically apply one queued proposal card."""
    proposal = proposal_for_task(task)
    if proposal is None:
        raise ProtocolError(f"task is not a roster proposal: {task.id}")
    if task.status != "queued":
        raise ProtocolError("only a queued roster proposal can be approved")
    if task.holder != authorised_by:
        raise ProtocolError("only the proposal holder may authorise roster changes")

    update_task(
        db,
        task,
        who=authorised_by,
        is_privileged=False,
        status="doing",
        note="Roster proposal approved",
    )
    created = 0
    unchanged = 0
    for item in sorted(
        proposal["items"], key=lambda value: (_KIND_ORDER[value["kind"]], value["key"])
    ):
        if _apply_item(
            db,
            item,
            authorising_identity=authorised_by,
            performing_agent=performed_by,
            proposal_task_id=task.id,
        ):
            created += 1
        else:
            unchanged += 1
    update_task(
        db,
        task,
        who=authorised_by,
        is_privileged=False,
        status="done",
        note=f"Automation applied {created} approved roster additions",
        performing_agent=performed_by,
    )
    return {"created": created, "unchanged": unchanged}
