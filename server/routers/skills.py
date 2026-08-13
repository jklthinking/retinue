"""Skill registry, actor bindings, import provenance, and claim briefing."""

from __future__ import annotations

import json
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from core.protocol.task import ProtocolError

from ..db import Skill
from ..deps import Principal, get_db, require_admin, require_auth, wrap_protocol_errors
from ..schemas import SkillBindBody, SkillBindingUpdateBody, SkillBody, SkillImportBody
from ..skill_ops import (
    actor_skill_briefing,
    apply_pilot_bindings,
    bind_skill,
    binding_to_dict,
    event_to_dict,
    import_runtime_skill,
    list_binding_events,
    list_bindings,
    set_binding_enabled,
    skill_to_dict,
    unbind_skill,
)

router = APIRouter()


@router.get("/api/skills")
def get_skills(
    principal: Principal = Depends(require_auth), db: Session = Depends(get_db, scope="function")
) -> list[dict[str, Any]]:
    return [
        skill_to_dict(s)
        for s in db.execute(select(Skill).order_by(Skill.category, Skill.name)).scalars()
    ]


@router.post("/api/skills")
def post_skill(
    body: SkillBody,
    principal: Principal = Depends(require_auth),
    db: Session = Depends(get_db, scope="function"),
) -> dict[str, Any]:
    skill = db.execute(select(Skill).where(Skill.name == body.name)).scalar()
    owners = body.owners or ([principal.write_identity] if principal.kind == "agent" else [])
    if skill is None:
        skill = Skill(
            name=body.name,
            description=body.description,
            category=body.category,
            enabled=body.enabled,
            owners_json=json.dumps(owners),
        )
        db.add(skill)
    else:
        if principal.kind == "agent" and principal.write_identity not in json.loads(
            skill.owners_json
        ):
            raise HTTPException(status_code=403, detail="only owners may update a skill")
        skill.description = body.description or skill.description
        skill.category = body.category or skill.category
        skill.enabled = body.enabled
        if owners:
            skill.owners_json = json.dumps(owners)
    db.flush()
    return skill_to_dict(skill)


@router.post("/api/skills/import")
def post_skill_import(
    body: SkillImportBody,
    principal: Principal = Depends(require_auth),
    db: Session = Depends(get_db, scope="function"),
) -> dict[str, Any]:
    existing = db.execute(select(Skill).where(Skill.name == body.name)).scalar()
    if existing is not None and principal.kind == "agent":
        owners = json.loads(existing.owners_json or "[]")
        if principal.write_identity not in owners:
            raise HTTPException(status_code=403, detail="only owners may update a skill")
    try:
        skill = import_runtime_skill(
            db,
            name=body.name,
            who=principal.write_identity,
            description=body.description,
            category=body.category,
            source=body.source,
            source_kind=body.source_kind,
            snapshot=body.snapshot,
            enabled=body.enabled,
            owners=body.owners
            or ([principal.write_identity] if principal.kind == "agent" else []),
        )
    except ProtocolError as exc:
        raise wrap_protocol_errors(exc) from exc
    return skill_to_dict(skill)


@router.get("/api/me/skill-briefing")
def get_my_skill_briefing(
    principal: Principal = Depends(require_auth),
    db: Session = Depends(get_db, scope="function"),
) -> dict[str, Any]:
    try:
        return actor_skill_briefing(db, principal.write_identity)
    except ProtocolError as exc:
        raise wrap_protocol_errors(exc) from exc


@router.get("/api/actors/{actor_id}/skills")
def get_actor_skills(
    actor_id: str,
    principal: Principal = Depends(require_auth),
    db: Session = Depends(get_db, scope="function"),
) -> list[dict[str, Any]]:
    try:
        return [binding_to_dict(binding, skill) for binding, skill in list_bindings(db, actor_id)]
    except ProtocolError as exc:
        raise wrap_protocol_errors(exc) from exc


@router.post("/api/actors/{actor_id}/skills")
def post_actor_skill(
    actor_id: str,
    body: SkillBindBody,
    principal: Principal = Depends(require_admin),
    db: Session = Depends(get_db, scope="function"),
) -> dict[str, Any]:
    try:
        binding = bind_skill(
            db,
            actor_id=actor_id,
            who=principal.write_identity,
            skill_id=body.skill_id,
            name=body.name,
            enabled=body.enabled,
        )
        skill = db.get(Skill, binding.skill_id)
        assert skill is not None
        return binding_to_dict(binding, skill)
    except ProtocolError as exc:
        raise wrap_protocol_errors(exc) from exc


@router.post("/api/actors/{actor_id}/skills/{skill_id}/update")
def post_actor_skill_update(
    actor_id: str,
    skill_id: int,
    body: SkillBindingUpdateBody,
    principal: Principal = Depends(require_admin),
    db: Session = Depends(get_db, scope="function"),
) -> dict[str, Any]:
    try:
        binding = set_binding_enabled(
            db,
            actor_id=actor_id,
            skill_id=skill_id,
            enabled=body.enabled,
            who=principal.write_identity,
        )
        skill = db.get(Skill, binding.skill_id)
        assert skill is not None
        return binding_to_dict(binding, skill)
    except ProtocolError as exc:
        raise wrap_protocol_errors(exc) from exc


@router.post("/api/actors/{actor_id}/skills/{skill_id}/unbind")
def post_actor_skill_unbind(
    actor_id: str,
    skill_id: int,
    principal: Principal = Depends(require_admin),
    db: Session = Depends(get_db, scope="function"),
) -> dict[str, Any]:
    try:
        unbind_skill(
            db, actor_id=actor_id, skill_id=skill_id, who=principal.write_identity
        )
    except ProtocolError as exc:
        raise wrap_protocol_errors(exc) from exc
    return {"ok": True, "actor_id": actor_id, "skill_id": skill_id}


@router.get("/api/actors/{actor_id}/skill-events")
def get_actor_skill_events(
    actor_id: str,
    principal: Principal = Depends(require_auth),
    db: Session = Depends(get_db, scope="function"),
) -> list[dict[str, Any]]:
    try:
        return [event_to_dict(event) for event in list_binding_events(db, actor_id)]
    except ProtocolError as exc:
        raise wrap_protocol_errors(exc) from exc


@router.post("/api/skills/pilot-bindings")
def post_pilot_bindings(
    principal: Principal = Depends(require_admin),
    db: Session = Depends(get_db, scope="function"),
) -> dict[str, Any]:
    applied = apply_pilot_bindings(db, who=principal.write_identity)
    return {"ok": True, "applied": applied, "count": len(applied)}
