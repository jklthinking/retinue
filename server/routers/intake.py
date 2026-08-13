"""Generic inbound-channel webhook adapter (intake protocol M0, layer 2).

One route serves every entry channel: the channel id comes from the path and
must match the channel credential, the sender id is resolved through the
channel-user mapping, and the message opens an open-dispatch hall card signed
by the mapped board user. Feishu is the first channel; the adapter stays
vendor-free — platform signature verification plugs into the shared-secret
placeholder in ``server/intake.py`` once real credentials are configured.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session

from ..deps import Principal, get_db, require_channel, wrap_protocol_errors
from ..engine import Forbidden
from ..helpers import task_response
from ..intake import (
    TITLE_LIMIT,
    channel_secret_configured,
    message_digest,
    open_channel_card,
)
from ..schemas import IntakeMessageBody

router = APIRouter()


def _check_channel_secret(request: Request, channel_id: str) -> None:
    """Optional per-channel shared secret (configuration placeholder)."""
    secret = channel_secret_configured(channel_id)
    if secret is None:
        return
    if request.headers.get("x-intake-secret", "") != secret:
        raise HTTPException(status_code=403, detail="通道密钥校验失败")


@router.post("/api/intake/{channel_id}/webhook")
def post_intake_webhook(
    channel_id: str,
    body: IntakeMessageBody,
    request: Request,
    principal: Principal = Depends(require_channel),
    db: Session = Depends(get_db, scope="function"),
) -> dict[str, Any]:
    """Turn one channel message into one hall card; reply carries the card id."""
    if principal.name != channel_id:
        raise HTTPException(
            status_code=403, detail="channel token belongs to another channel"
        )
    _check_channel_secret(request, channel_id)
    title = body.text.strip().splitlines()[0][:TITLE_LIMIT]
    note = (
        f"通道开卡 [{channel_id}] 用户 {body.sender_id}: "
        + message_digest(body.text, body.message_id)
    )
    try:
        task = open_channel_card(
            db,
            channel_id=channel_id,
            channel_user_id=body.sender_id,
            title=title,
            note=note,
            acceptance=[f"原始消息: {message_digest(body.text, body.message_id)}"],
            event_key=f"intake:{channel_id}:{body.message_id}",
        )
    except Forbidden as exc:
        raise wrap_protocol_errors(exc) from exc
    result = task_response(task)
    return {
        "task_id": task.id,
        "status": task.status,
        "created_by": task.created_by,
        "receipt": result["receipt"],
    }
