"""Deterministic intent-to-pipeline dispatch with source-event idempotency."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from core.protocol.task import ProtocolError

from .db import DispatchRequest, PipelineTemplate, Task
from .engine import Conflict, create_task
from .flow import validate_pipeline


@dataclass(frozen=True)
class DispatchOutcome:
    task: Task
    template: PipelineTemplate
    matched_terms: list[str]
    created: bool


def _normalized(value: str) -> str:
    return " ".join(value.casefold().split())


def _fingerprint(
    *,
    intent: str,
    template_name: str | None,
    priority: str,
    acceptance: list[str],
) -> str:
    payload = {
        "intent": _normalized(intent),
        "template_name": _normalized(template_name or ""),
        "priority": priority,
        "acceptance": [item.strip() for item in acceptance],
    }
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _template_match(
    db: Session, intent: str, explicit_name: str | None
) -> tuple[PipelineTemplate, list[str]]:
    templates = list(
        db.execute(select(PipelineTemplate).order_by(PipelineTemplate.id)).scalars()
    )
    if explicit_name:
        wanted = _normalized(explicit_name)
        exact = [item for item in templates if _normalized(item.name) == wanted]
        if not exact:
            raise ProtocolError(f"unknown pipeline template: {explicit_name}")
        return exact[0], []

    normalized_intent = _normalized(intent)
    ranked: list[tuple[int, int, PipelineTemplate, list[str]]] = []
    for template in templates:
        terms = [term.strip() for term in template.match_terms if term.strip()]
        matched = [term for term in terms if _normalized(term) in normalized_intent]
        name_hit = _normalized(template.name) in normalized_intent
        score = (100 if name_hit else 0) + sum(len(term) for term in matched)
        if score:
            ranked.append((score, len(matched), template, matched))
    if not ranked:
        raise ProtocolError("no pipeline template matched the intent; pass template_name")
    ranked.sort(key=lambda row: (row[0], row[1], -row[2].id), reverse=True)
    top = ranked[0]
    tied = [row for row in ranked if row[:2] == top[:2]]
    if len(tied) > 1:
        names = ", ".join(row[2].name for row in tied)
        raise ProtocolError(f"ambiguous pipeline template match: {names}")
    return top[2], top[3]


def _request_hash(
    intent: str,
    template_name: str | None,
    priority: str,
    acceptance: list[str] | None,
) -> tuple[str, list[str]]:
    criteria = [item.strip() for item in (acceptance or []) if item.strip()]
    return _fingerprint(
        intent=" ".join(intent.split()),
        template_name=template_name,
        priority=priority,
        acceptance=criteria,
    ), criteria


def _replay(
    db: Session, *, actor_id: str, idempotency_key: str, request_hash: str
) -> DispatchOutcome | None:
    record = db.execute(
        select(DispatchRequest)
        .where(DispatchRequest.actor_id == actor_id)
        .where(DispatchRequest.idempotency_key == idempotency_key)
    ).scalar()
    if record is None:
        return None
    if record.request_hash != request_hash:
        raise Conflict("idempotency key was already used for a different dispatch request")
    task = db.get(Task, record.task_id)
    template = db.get(PipelineTemplate, record.template_id)
    if task is None or template is None:
        raise ProtocolError("idempotency record points to missing canonical state")
    terms = [
        term
        for term in template.match_terms
        if _normalized(term) in _normalized(task.title)
    ]
    return DispatchOutcome(task=task, template=template, matched_terms=terms, created=False)


def dispatch_intent(
    db: Session,
    *,
    actor_id: str,
    intent: str,
    idempotency_key: str,
    template_name: str | None = None,
    priority: str = "none",
    acceptance: list[str] | None = None,
) -> DispatchOutcome:
    """Create one pipeline card per ``(actor_id, idempotency_key)``."""
    normalized_intent = " ".join(intent.split())
    if not normalized_intent:
        raise ProtocolError("intent must not be empty")
    request_hash, criteria = _request_hash(
        normalized_intent, template_name, priority, acceptance
    )
    replay = _replay(
        db,
        actor_id=actor_id,
        idempotency_key=idempotency_key,
        request_hash=request_hash,
    )
    if replay is not None:
        return replay

    template, matched_terms = _template_match(db, normalized_intent, template_name)
    stages = validate_pipeline(db, json.loads(template.stages_json))
    task = create_task(
        db,
        title=normalized_intent[:256],
        created_by=actor_id,
        holder=stages[0]["holder"],
        priority=priority,
        acceptance=criteria or template.acceptance,
        note=f"dispatch:{idempotency_key}:{template.name}",
    )
    task.pipeline_json = json.dumps(stages, ensure_ascii=False)
    task.pipeline_stage = 0
    db.add(
        DispatchRequest(
            actor_id=actor_id,
            idempotency_key=idempotency_key,
            request_hash=request_hash,
            task_id=task.id,
            template_id=template.id,
        )
    )
    db.flush()
    return DispatchOutcome(
        task=task,
        template=template,
        matched_terms=matched_terms,
        created=True,
    )


def replay_after_conflict(
    db: Session,
    *,
    actor_id: str,
    intent: str,
    idempotency_key: str,
    template_name: str | None = None,
    priority: str = "none",
    acceptance: list[str] | None = None,
) -> DispatchOutcome:
    """Resolve a concurrent unique-key race after the losing transaction rolls back."""
    request_hash, _criteria = _request_hash(intent, template_name, priority, acceptance)
    outcome = _replay(
        db,
        actor_id=actor_id,
        idempotency_key=idempotency_key,
        request_hash=request_hash,
    )
    if outcome is None:
        raise Conflict("dispatch idempotency race could not be resolved")
    return outcome
