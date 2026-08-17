"""Attention inbox route: one GET folds the four attention lanes together.

The aggregation itself is read-only and follows the same login-session rule
as the summary read model (``require_auth``). The same GET also nudges the
daily digest: ``run_daily_digest`` registers one date-keyed reminder slot per
owner account and lets the existing reminders scanner deliver it through the
configured channels (``in_app`` by default). Both steps are idempotent, so
polling this endpoint can never duplicate a digest; a digest failure is
reported in the ``digest`` section and never breaks the lanes read.

Response models live in this module on purpose — ``server/schemas.py`` holds
request bodies and is owned by another workstream.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from ..deps import Principal, get_db, require_auth
from ..inbox import collect_inbox, run_daily_digest

logger = logging.getLogger(__name__)

router = APIRouter()


class InboxLane(BaseModel):
    """One attention lane: total count plus the first rows."""

    count: int
    items: list[dict[str, Any]]


class InboxLanes(BaseModel):
    decisions: InboxLane  # 待拍板: queen-gate approvals awaiting a decision
    reviews: InboxLane  # 待质检: QC comments without a reply yet
    blocked: InboxLane  # 阻塞待解: blocked cards with their reason
    stale: InboxLane  # 超期未动: doing cards overdue or heartbeat-lost


class InboxDigest(BaseModel):
    """Outcome of the daily-digest nudge attached to this read."""

    date: str
    enabled: bool
    registered: int
    owners: int
    delivered: int


class InboxResponse(BaseModel):
    generated_at: str
    today: str
    lanes: InboxLanes
    digest: InboxDigest


@router.get("/api/inbox", response_model=InboxResponse)
def get_inbox(
    lane_limit: int = Query(default=5, ge=1, le=50),
    principal: Principal = Depends(require_auth),
    db: Session = Depends(get_db, scope="function"),
) -> InboxResponse:
    data = collect_inbox(db, lane_limit=lane_limit)
    try:
        digest = run_daily_digest(db)
    except Exception as exc:  # noqa: BLE001 — digest must not break the read
        logger.warning("inbox digest nudge failed: %s", exc.__class__.__name__)
        db.rollback()
        digest = {
            "date": data["today"],
            "enabled": False,
            "registered": 0,
            "owners": 0,
            "delivered": 0,
        }
    return InboxResponse(
        generated_at=data["generated_at"],
        today=data["today"],
        lanes=InboxLanes(**data["lanes"]),
        digest=InboxDigest(**digest),
    )
