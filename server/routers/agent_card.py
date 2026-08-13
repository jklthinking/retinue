"""Well-known agent card: A2A-style discovery, off unless an operator opts in.

Serves ``GET /.well-known/agent-card.json`` only when the operator sets
``RETINUE_AGENT_CARD`` to a truthy value. The card is built from the skills
registry that already exists; there is no second registry. It publishes no
actor, node, token, address, or path material, and it carries no
``supportedInterfaces`` entry because this deployment serves no A2A task
endpoint — the card is discovery-only. See ``docs/protocol/a2a-discovery.md``
for the identity decision, the per-field safety argument, and the vocabulary
mapping.
"""

from __future__ import annotations

import os
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import __version__
from ..db import Skill
from ..deps import get_db, site_config

router = APIRouter()

_TRUE_VALUES = {"1", "true", "yes", "on"}


def agent_card_enabled() -> bool:
    """Read per request so tests and operators toggle without a reload."""
    return os.environ.get("RETINUE_AGENT_CARD", "").strip().lower() in _TRUE_VALUES


def build_card(skills: list[Skill], label: str) -> dict[str, Any]:
    """One card for the whole deployment, listing the registry's skills.

    Every field is either a constant, the server version, the operator-set
    site label, or operator-curated registry text (skill name, description,
    category). Nothing derived from actors, nodes, tokens, paths, or network
    addresses appears here.
    """
    return {
        "name": label.strip() or "retinue",
        "description": (
            "Retinue multi-agent coordination server: an append-only task "
            "board with a single-holder baton, an approval gate, and a skill "
            "registry. This card is discovery-only. The deployment serves no "
            "A2A task endpoint, so a client can learn what this agent hosts "
            "but cannot submit work over A2A."
        ),
        "version": __version__,
        "capabilities": {
            "streaming": False,
            "pushNotifications": False,
            "extendedAgentCard": False,
        },
        "defaultInputModes": ["text/plain"],
        "defaultOutputModes": ["text/plain"],
        "skills": [
            {
                "id": skill.name,
                "name": skill.name,
                "description": skill.description,
                "tags": [skill.category],
            }
            for skill in skills
        ],
    }


@router.get("/.well-known/agent-card.json")
def agent_card(
    request: Request, db: Session = Depends(get_db, scope="function")
) -> dict[str, Any]:
    if not agent_card_enabled():
        raise HTTPException(status_code=404, detail="not found")
    config = site_config(request.app.state.data_dir)
    label = config.get("label", "") if isinstance(config.get("label", ""), str) else ""
    skills = db.execute(
        select(Skill).where(Skill.enabled.is_(True)).order_by(Skill.category, Skill.name)
    ).scalars()
    return build_card(list(skills), label)
