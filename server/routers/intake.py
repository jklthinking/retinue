"""Generic inbound-channel webhook adapter (intake protocol M0/M1, layer 2).

One route serves every entry channel: the channel id comes from the path and
must match the channel credential. M0 opens a hall card for plain messages;
M1 additionally parses progress / note / status / done commands from the first
line of the message text. Feishu is the first channel; the adapter stays
vendor-free — platform signature verification plugs into the shared-secret
placeholder in ``server/intake.py`` once real credentials are configured.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session

from ..deps import Principal, get_db, require_channel, wrap_protocol_errors
from ..engine import Forbidden, ProtocolError
from ..intake import UnmappedChannelUser, channel_secret_configured
from ..intake_commands import handle_intake_message
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
    """Dispatch one channel message: open a hall card or apply an M1 command."""
    if principal.name != channel_id:
        raise HTTPException(
            status_code=403, detail="channel token belongs to another channel"
        )
    _check_channel_secret(request, channel_id)
    try:
        return handle_intake_message(
            db,
            channel_id=channel_id,
            channel_user_id=body.sender_id,
            text=body.text,
            message_id=body.message_id,
        )
    except UnmappedChannelUser as exc:
        # Machine-readable marker for inbound bridges: this refusal means
        # "reply the registration guide", not "delivery failed". The human
        # detail string stays unchanged for direct API callers.
        raise HTTPException(
            status_code=403,
            detail=str(exc),
            headers={"X-Intake-Error": "channel-user-unmapped"},
        ) from exc
    except Forbidden as exc:
        raise wrap_protocol_errors(exc) from exc
    except ProtocolError as exc:
        raise wrap_protocol_errors(exc) from exc