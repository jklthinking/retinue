"""Pipeline template routes."""

from __future__ import annotations

import json
from typing import Any

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from core.protocol.task import ProtocolError

from ..db import PipelineTemplate
from ..deps import Principal, get_db, require_admin, require_auth, wrap_protocol_errors
from ..flow import validate_pipeline
from ..schemas import PipelineTemplateBody

router = APIRouter()


@router.get("/api/pipeline-templates")
def get_pipeline_templates(
    principal: Principal = Depends(require_auth), db: Session = Depends(get_db, scope="function")
) -> list[dict[str, Any]]:
    return [
        {
            "id": t.id,
            "name": t.name,
            "stages": json.loads(t.stages_json),
            "match_terms": t.match_terms,
            "acceptance": t.acceptance,
        }
        for t in db.execute(select(PipelineTemplate).order_by(PipelineTemplate.name)).scalars()
    ]


@router.post("/api/pipeline-templates")
def post_pipeline_template(
    body: PipelineTemplateBody,
    principal: Principal = Depends(require_admin),
    db: Session = Depends(get_db, scope="function"),
) -> dict[str, Any]:
    try:
        stages = validate_pipeline(db, [s.model_dump() for s in body.stages])
    except ProtocolError as exc:
        raise wrap_protocol_errors(exc) from exc
    template = db.execute(
        select(PipelineTemplate).where(PipelineTemplate.name == body.name)
    ).scalar()
    if template is None:
        template = PipelineTemplate(name=body.name)
        db.add(template)
    match_terms = list(dict.fromkeys(
        term.strip() for term in body.match_terms if term.strip()
    ))
    acceptance = list(dict.fromkeys(
        item.strip() for item in body.acceptance if item.strip()
    ))
    template.stages_json = json.dumps(stages, ensure_ascii=False)
    template.match_terms_json = json.dumps(match_terms, ensure_ascii=False)
    template.acceptance_json = json.dumps(acceptance, ensure_ascii=False)
    db.flush()
    return {
        "id": template.id,
        "name": template.name,
        "stages": stages,
        "match_terms": match_terms,
        "acceptance": acceptance,
    }
