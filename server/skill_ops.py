"""Actor-skill bindings, import provenance, and claim-time briefing.

Bindings are the Multica-shaped assignment: one skill may serve many
executors, each assignment can be paused without deleting the catalog row,
and only identities that may edit an executor may change its bindings.
"""

from __future__ import annotations

import json
from typing import Any, Iterable

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from core.protocol.task import ProtocolError, validate_ledger_text

from .db import Actor, Skill, SkillBinding, SkillBindingEvent, utcnow


BINDING_ACTIONS = ("bind", "unbind", "enable", "disable")
SOURCE_KINDS = ("local", "workspace", "repo", "runtime", "external")
UNREVIEWED_SOURCE_KINDS = frozenset({"repo", "runtime", "external"})
SNAPSHOT_FIELDS = ("name", "description", "category", "origin_label", "checksum", "version")
RISK_NOTICE = (
    "This skill comes from an unreviewed source and is not sandboxed. "
    "Trust the importer and the origin before enabling it on an executor."
)

# First operating set: inventory names already present in the live catalog.
# Applied only when both the actor and the skill exist in the target database.
PILOT_BINDINGS: tuple[tuple[str, tuple[str, ...]], ...] = (
    (
        "throne-codex",
        (
            "test-driven-development",
            "systematic-debugging",
            "github-pr-workflow",
        ),
    ),
    (
        "windows-cursor",
        (
            "review",
            "plan",
            "github-pr-collaboration-workflow",
        ),
    ),
)


def skill_risk_notice(skill: Skill) -> str | None:
    kind = (skill.source_kind or "local").strip() or "local"
    if kind in UNREVIEWED_SOURCE_KINDS:
        return RISK_NOTICE
    return None


def skill_to_dict(skill: Skill) -> dict[str, Any]:
    try:
        owners = json.loads(skill.owners_json or "[]")
    except (TypeError, json.JSONDecodeError):
        owners = []
    try:
        snapshot = json.loads(skill.source_snapshot_json or "{}")
    except (TypeError, json.JSONDecodeError):
        snapshot = {}
    if not isinstance(snapshot, dict):
        snapshot = {}
    return {
        "id": skill.id,
        "name": skill.name,
        "description": skill.description,
        "category": skill.category,
        "enabled": skill.enabled,
        "owners": owners,
        "source": skill.source,
        "source_kind": skill.source_kind or "local",
        "source_snapshot": snapshot,
        "imported_by": skill.imported_by,
        "imported_at": skill.imported_at.isoformat() if skill.imported_at else None,
        "risk_notice": skill_risk_notice(skill),
        "updated_at": skill.updated_at.isoformat() if skill.updated_at else None,
    }


def binding_to_dict(binding: SkillBinding, skill: Skill) -> dict[str, Any]:
    row = skill_to_dict(skill)
    row["binding_id"] = binding.id
    row["binding_enabled"] = binding.enabled
    row["bound_by"] = binding.created_by
    row["bound_at"] = binding.created_at.isoformat() if binding.created_at else None
    return row


def event_to_dict(event: SkillBindingEvent) -> dict[str, Any]:
    try:
        payload = json.loads(event.payload_json or "{}")
    except (TypeError, json.JSONDecodeError):
        payload = {}
    return {
        "seq": event.seq,
        "actor_id": event.actor_id,
        "skill_id": event.skill_id,
        "skill_name": event.skill_name,
        "action": event.action,
        "who": event.who,
        "did": event.did,
        "from_enabled": event.from_enabled,
        "to_enabled": event.to_enabled,
        "payload": payload,
        "at": event.at.isoformat() if event.at else None,
    }


def _require_actor(db: Session, actor_id: str) -> Actor:
    actor = db.get(Actor, actor_id)
    if actor is None or actor.disabled:
        raise ProtocolError(f"unknown or disabled actor: {actor_id}")
    return actor


def _resolve_skill(
    db: Session, *, skill_id: int | None = None, name: str | None = None
) -> Skill:
    if skill_id is not None:
        skill = db.get(Skill, skill_id)
        if skill is None:
            raise ProtocolError(f"unknown skill: {skill_id}")
        return skill
    if name:
        skill = db.execute(select(Skill).where(Skill.name == name)).scalar()
        if skill is None:
            raise ProtocolError(f"unknown skill: {name}")
        return skill
    raise ProtocolError("skill_id or name is required")


def _next_event_seq(db: Session, actor_id: str) -> int:
    latest = db.execute(
        select(func.max(SkillBindingEvent.seq)).where(
            SkillBindingEvent.actor_id == actor_id
        )
    ).scalar()
    return int(latest or 0) + 1


def _append_event(
    db: Session,
    *,
    actor_id: str,
    skill: Skill,
    action: str,
    who: str,
    did: str,
    from_enabled: bool | None,
    to_enabled: bool | None,
) -> SkillBindingEvent:
    if action not in BINDING_ACTIONS:
        raise ProtocolError(f"unknown binding action: {action}")
    note = validate_ledger_text(did, "did")
    event = SkillBindingEvent(
        actor_id=actor_id,
        seq=_next_event_seq(db, actor_id),
        skill_id=skill.id,
        skill_name=skill.name,
        action=action,
        who=who,
        did=note,
        from_enabled=from_enabled,
        to_enabled=to_enabled,
        payload_json=json.dumps(
            {"actor_id": actor_id, "skill_name": skill.name},
            ensure_ascii=False,
        ),
        at=utcnow(),
    )
    db.add(event)
    db.flush()
    return event


def bind_skill(
    db: Session,
    *,
    actor_id: str,
    who: str,
    skill_id: int | None = None,
    name: str | None = None,
    enabled: bool = True,
) -> SkillBinding:
    """Single funnel for assigning a catalog skill to an executor."""
    _require_actor(db, actor_id)
    skill = _resolve_skill(db, skill_id=skill_id, name=name)
    existing = db.execute(
        select(SkillBinding).where(
            SkillBinding.actor_id == actor_id,
            SkillBinding.skill_id == skill.id,
        )
    ).scalar()
    if existing is not None:
        if existing.enabled != enabled:
            return set_binding_enabled(
                db, actor_id=actor_id, skill_id=skill.id, enabled=enabled, who=who
            )
        return existing
    binding = SkillBinding(
        actor_id=actor_id,
        skill_id=skill.id,
        enabled=enabled,
        created_by=who,
    )
    db.add(binding)
    db.flush()
    _append_event(
        db,
        actor_id=actor_id,
        skill=skill,
        action="bind",
        who=who,
        did=f"bound skill {skill.name} onto {actor_id}",
        from_enabled=None,
        to_enabled=enabled,
    )
    if not enabled:
        _append_event(
            db,
            actor_id=actor_id,
            skill=skill,
            action="disable",
            who=who,
            did=f"disabled skill {skill.name} on {actor_id}",
            from_enabled=True,
            to_enabled=False,
        )
    return binding


def set_binding_enabled(
    db: Session,
    *,
    actor_id: str,
    skill_id: int,
    enabled: bool,
    who: str,
) -> SkillBinding:
    _require_actor(db, actor_id)
    binding = db.execute(
        select(SkillBinding).where(
            SkillBinding.actor_id == actor_id,
            SkillBinding.skill_id == skill_id,
        )
    ).scalar()
    if binding is None:
        raise ProtocolError("skill is not bound to this actor")
    if binding.enabled == enabled:
        return binding
    skill = db.get(Skill, skill_id)
    if skill is None:
        raise ProtocolError(f"unknown skill: {skill_id}")
    previous = binding.enabled
    binding.enabled = enabled
    binding.updated_at = utcnow()
    action = "enable" if enabled else "disable"
    verb = "enabled" if enabled else "disabled"
    _append_event(
        db,
        actor_id=actor_id,
        skill=skill,
        action=action,
        who=who,
        did=f"{verb} skill {skill.name} on {actor_id}",
        from_enabled=previous,
        to_enabled=enabled,
    )
    db.flush()
    return binding


def unbind_skill(
    db: Session, *, actor_id: str, skill_id: int, who: str
) -> None:
    _require_actor(db, actor_id)
    binding = db.execute(
        select(SkillBinding).where(
            SkillBinding.actor_id == actor_id,
            SkillBinding.skill_id == skill_id,
        )
    ).scalar()
    if binding is None:
        raise ProtocolError("skill is not bound to this actor")
    skill = db.get(Skill, skill_id)
    if skill is None:
        raise ProtocolError(f"unknown skill: {skill_id}")
    previous = binding.enabled
    db.delete(binding)
    _append_event(
        db,
        actor_id=actor_id,
        skill=skill,
        action="unbind",
        who=who,
        did=f"unbound skill {skill.name} from {actor_id}",
        from_enabled=previous,
        to_enabled=None,
    )
    db.flush()


def list_bindings(db: Session, actor_id: str) -> list[tuple[SkillBinding, Skill]]:
    _require_actor(db, actor_id)
    rows = db.execute(
        select(SkillBinding, Skill)
        .join(Skill, Skill.id == SkillBinding.skill_id)
        .where(SkillBinding.actor_id == actor_id)
        .order_by(Skill.category, Skill.name)
    ).all()
    return [(binding, skill) for binding, skill in rows]


def list_binding_events(db: Session, actor_id: str) -> list[SkillBindingEvent]:
    _require_actor(db, actor_id)
    return list(
        db.execute(
            select(SkillBindingEvent)
            .where(SkillBindingEvent.actor_id == actor_id)
            .order_by(SkillBindingEvent.seq)
        ).scalars()
    )


def enabled_bound_skills(db: Session, actor_id: str) -> list[Skill]:
    """Catalog-enabled skills whose binding on this actor is also enabled."""
    rows = list_bindings(db, actor_id)
    return [
        skill
        for binding, skill in rows
        if binding.enabled and skill.enabled
    ]


def actors_with_bindings(db: Session) -> set[str]:
    return set(db.execute(select(SkillBinding.actor_id).distinct()).scalars())


def bound_skills_by_actor(db: Session) -> dict[str, list[Skill]]:
    """Enabled bindings only; actors with any binding row are present as keys."""
    grouped: dict[str, list[Skill]] = {}
    rows = db.execute(
        select(SkillBinding, Skill)
        .join(Skill, Skill.id == SkillBinding.skill_id)
        .order_by(SkillBinding.actor_id, Skill.name)
    ).all()
    for binding, skill in rows:
        grouped.setdefault(binding.actor_id, [])
        if binding.enabled and skill.enabled:
            grouped[binding.actor_id].append(skill)
    return grouped


def actor_skill_briefing(db: Session, actor_id: str) -> dict[str, Any]:
    """Payload attached to a claim so the executor receives bound skills."""
    actor = _require_actor(db, actor_id)
    skills = enabled_bound_skills(db, actor_id)
    return {
        "actor_id": actor.id,
        "display_name": actor.display_name or actor.id,
        "skills": [skill_to_dict(skill) for skill in skills],
        "count": len(skills),
        "note": "Bindings take effect on subsequent runs and do not change work already started.",
    }


def sanitize_snapshot(raw: dict[str, Any] | None) -> dict[str, str]:
    if not raw:
        return {}
    if not isinstance(raw, dict):
        raise ProtocolError("snapshot must be an object")
    cleaned: dict[str, str] = {}
    for field in SNAPSHOT_FIELDS:
        if field not in raw:
            continue
        value = raw[field]
        if not isinstance(value, str):
            raise ProtocolError(f"snapshot.{field} must be a string")
        cleaned[field] = validate_ledger_text(
            value, f"snapshot.{field}", max_length=500
        )
    return cleaned


def import_runtime_skill(
    db: Session,
    *,
    name: str,
    who: str,
    description: str = "",
    category: str = "",
    source: str = "local",
    source_kind: str = "runtime",
    snapshot: dict[str, Any] | None = None,
    enabled: bool = True,
    owners: Iterable[str] | None = None,
) -> Skill:
    """Create or refresh a catalog row from a runtime import, keeping provenance."""
    if source_kind not in SOURCE_KINDS:
        raise ProtocolError(f"unknown source_kind: {source_kind}")
    name = validate_ledger_text(name, "name", max_length=128)
    if description:
        description = validate_ledger_text(description, "description", max_length=2000)
    if category:
        category = validate_ledger_text(category, "category", max_length=64)
    cleaned = sanitize_snapshot(snapshot)
    if "name" not in cleaned:
        cleaned["name"] = name
    owner_list = [str(item) for item in (owners or []) if str(item).strip()]
    existing = db.execute(select(Skill).where(Skill.name == name)).scalar()
    now = utcnow()
    if existing is None:
        skill = Skill(
            name=name,
            description=description,
            category=category,
            enabled=enabled,
            owners_json=json.dumps(owner_list or [who]),
            source=source,
            source_kind=source_kind,
            source_snapshot_json=json.dumps(cleaned, ensure_ascii=False),
            imported_by=who,
            imported_at=now,
        )
        db.add(skill)
        db.flush()
        return skill
    existing.description = description or existing.description
    existing.category = category or existing.category
    existing.enabled = enabled
    existing.source = source
    existing.source_kind = source_kind
    existing.source_snapshot_json = json.dumps(cleaned, ensure_ascii=False)
    existing.imported_by = who
    existing.imported_at = now
    if owner_list:
        existing.owners_json = json.dumps(owner_list)
    db.flush()
    return existing


def apply_pilot_bindings(db: Session, *, who: str) -> list[dict[str, str]]:
    """Bind the first operating set when both sides exist. Missing rows are skipped."""
    applied: list[dict[str, str]] = []
    for actor_id, names in PILOT_BINDINGS:
        if db.get(Actor, actor_id) is None:
            continue
        for name in names:
            skill = db.execute(select(Skill).where(Skill.name == name)).scalar()
            if skill is None:
                continue
            bind_skill(db, actor_id=actor_id, name=name, who=who, enabled=True)
            applied.append({"actor_id": actor_id, "name": name})
    return applied
