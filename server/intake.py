"""Intake protocol M0: channel-side card opening.

Layer 1 (channel identity) lives in ``deps.py`` and the ``channel_tokens`` /
``channel_users`` tables; this module is layer 2, the publish specification:
a channel opens a card through the same engine funnel as every other card,
always as open dispatch, always signed by the mapped board user, always with
the original message digest in the first chain note. Layer 3 (executor
self-registration) lives in ``routers/enroll.py``.

The webhook adapter is a generic skeleton. Channel-specific verification is a
configuration placeholder: set ``RETINUE_INTAKE_<CHANNEL>_SECRET`` and the
sender must echo it in the ``X-Intake-Secret`` header. A Feishu deployment
would additionally verify the platform signature here once real credentials
are configured; M0 ships no vendor credentials and is tested with simulated
requests only.
"""

from __future__ import annotations

import os
from typing import Iterable

from sqlalchemy import select
from sqlalchemy.orm import Session

from .db import Actor, ChannelUser, Task, TaskEvent
from .engine import Forbidden, create_task

# Title and digest bounds keep card text receipt-quality: one line, no bulk
# transcript copies on the card face (AGENTS.md rule 4).
TITLE_LIMIT = 120
DIGEST_LIMIT = 200


class IntakeRefused(Forbidden):
    """The channel user is not authorized to open cards on the board."""


def channel_secret_configured(channel_id: str) -> str | None:
    """Return the optional shared secret configured for one channel.

    Configuration placeholder for per-channel webhook verification. The env
    var name is derived from the channel id (``feishu-bot`` →
    ``RETINUE_INTAKE_FEISHU_BOT_SECRET``); unset means the skeleton runs
    without an extra shared secret and channel-token auth alone gates intake.
    """
    key = "RETINUE_INTAKE_" + "".join(
        ch if ch.isalnum() else "_" for ch in channel_id.upper()
    ) + "_SECRET"
    return os.environ.get(key, "").strip() or None


def message_digest(text: str, message_id: str) -> str:
    """One-line original-message summary with the platform backlink anchor."""
    summary = " ".join(text.split())[:DIGEST_LIMIT]
    return f"{summary} (回链: message_id={message_id})"


def resolve_channel_actor(
    db: Session, *, channel_id: str, channel_user_id: str
) -> Actor:
    """Resolve a channel-internal user identity to its mapped board actor."""
    mapping = db.execute(
        select(ChannelUser).where(
            ChannelUser.channel_id == channel_id,
            ChannelUser.channel_user_id == channel_user_id,
        )
    ).scalar()
    if mapping is None:
        raise IntakeRefused(
            f"通道用户未绑定板上身份: {channel_id}/{channel_user_id};"
            "请管理员先建立映射"
        )
    actor = db.get(Actor, mapping.actor_id)
    if actor is None or actor.disabled:
        raise IntakeRefused(
            f"通道用户映射的板上身份不可用: {mapping.actor_id!r}"
        )
    return actor


def open_channel_card(
    db: Session,
    *,
    channel_id: str,
    channel_user_id: str,
    title: str,
    note: str,
    acceptance: Iterable[str] = (),
    dept: str | None = None,
    priority: str = "none",
    event_key: str | None = None,
) -> Task:
    """Open one hall card on behalf of a mapped channel user.

    The card is always open-dispatch: the publishing user keeps the baton and
    the card waits in the hall for an executor. Provenance is recorded twice —
    on the row (``source_channel``/``source_user``) and in the first chain
    event payload — so reads and audits both see where the card came from.
    """
    actor = resolve_channel_actor(
        db, channel_id=channel_id, channel_user_id=channel_user_id
    )
    if event_key:
        existing_event = db.execute(
            select(TaskEvent).where(TaskEvent.event_key == event_key)
        ).scalar()
        if existing_event is not None:
            return db.get(Task, existing_event.task_id)
    task = create_task(
        db,
        title=title,
        created_by=actor.id,
        holder=actor.id,
        dept=dept,
        priority=priority,
        acceptance=acceptance,
        note=note,
        open_dispatch=True,
        event_key=event_key,
        event_payload={
            "source_channel": channel_id,
            "source_user": channel_user_id,
        },
    )
    task.source_channel = channel_id
    task.source_user = channel_user_id
    db.flush()
    return task
