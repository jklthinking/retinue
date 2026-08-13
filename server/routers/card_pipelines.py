"""HTTP routes for multi-card pipeline templates and instance checkpoints."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from core.protocol.task import ProtocolError

from ..db import CardPipelineInstance, CardPipelineTemplate
from ..deps import Principal, get_db, require_auth, wrap_protocol_errors
from ..pipeline_v2 import (
    get_instance_status,
    instantiate_card_pipeline,
    instance_to_dict,
    list_card_pipeline_templates,
    resume_instance,
    template_to_dict,
    upsert_card_pipeline_template,
)
from ..schemas import CardPipelineInstantiateBody, CardPipelineTemplateBody

router = APIRouter()


@router.get("/api/card-pipelines")
def get_card_pipelines(
    principal: Principal = Depends(require_auth),
    db: Session = Depends(get_db, scope="function"),
) -> list[dict[str, Any]]:
    del principal
    return list_card_pipeline_templates(db)


@router.post("/api/card-pipelines")
def post_card_pipeline(
    body: CardPipelineTemplateBody,
    principal: Principal = Depends(require_auth),
    db: Session = Depends(get_db, scope="function"),
) -> dict[str, Any]:
    try:
        row = upsert_card_pipeline_template(
            db,
            name=body.name,
            spec={"nodes": [node.model_dump() for node in body.nodes]},
            created_by=principal.write_identity,
        )
    except ProtocolError as exc:
        raise wrap_protocol_errors(exc) from exc
    return template_to_dict(row)


@router.get("/api/card-pipelines/{template_id}")
def get_card_pipeline(
    template_id: int,
    principal: Principal = Depends(require_auth),
    db: Session = Depends(get_db, scope="function"),
) -> dict[str, Any]:
    del principal
    row = db.get(CardPipelineTemplate, template_id)
    if row is None:
        raise HTTPException(status_code=404, detail="pipeline template not found")
    return template_to_dict(row)


@router.post("/api/card-pipelines/{template_id}/instantiate")
def post_card_pipeline_instantiate(
    template_id: int,
    body: CardPipelineInstantiateBody,
    principal: Principal = Depends(require_auth),
    db: Session = Depends(get_db, scope="function"),
) -> dict[str, Any]:
    row = db.get(CardPipelineTemplate, template_id)
    if row is None:
        raise HTTPException(status_code=404, detail="pipeline template not found")
    try:
        instance = instantiate_card_pipeline(
            db,
            row,
            created_by=principal.write_identity,
            instance_key=body.instance_key,
        )
    except ProtocolError as exc:
        raise wrap_protocol_errors(exc) from exc
    return instance_to_dict(db, instance, row)


@router.get("/api/card-pipeline-instances/{instance_id}")
def get_card_pipeline_instance(
    instance_id: int,
    principal: Principal = Depends(require_auth),
    db: Session = Depends(get_db, scope="function"),
) -> dict[str, Any]:
    del principal
    row = db.get(CardPipelineInstance, instance_id)
    if row is None:
        raise HTTPException(status_code=404, detail="pipeline instance not found")
    return get_instance_status(db, row)


@router.post("/api/card-pipeline-instances/{instance_id}/resume")
def post_card_pipeline_instance_resume(
    instance_id: int,
    principal: Principal = Depends(require_auth),
    db: Session = Depends(get_db, scope="function"),
) -> dict[str, Any]:
    row = db.get(CardPipelineInstance, instance_id)
    if row is None:
        raise HTTPException(status_code=404, detail="pipeline instance not found")
    try:
        return resume_instance(db, row, who=principal.write_identity)
    except ProtocolError as exc:
        raise wrap_protocol_errors(exc) from exc
