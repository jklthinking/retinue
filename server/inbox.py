"""Attention inbox aggregation and daily digest (M0).

One collector folds the four attention lanes into a single read model:
pending decisions (queen-gate approvals), QC reviews still waiting for a
reply, blocked cards with their reason, and in-flight (``doing``) cards that
are overdue or whose lease heartbeat has been lost. Lane predicates reuse the
summary read model's building blocks so the two first screens never disagree
about what a card is.

The daily digest reuses the reminder delivery facility instead of inventing a
scheduler: for every enabled owner account it registers one
``reminder_deliveries`` slot whose ``delivery_key`` embeds the calendar date
(``inbox-digest:<user>:<date>``), so a same-day rescan is a no-op and the
existing ``deliver_due_reminders`` scanner (already invoked from the
reclaim/ready sweep cadence) fans it out through the configured channels —
``in_app`` by default. The slot anchors to a deterministic per-user todo
(``inbox-digest-NNNNNN``) whose title carries the day's four lane counts, so
the delivered in-app message *is* the summary. Neither ``reminders.py`` nor
``dispatch_v2.py`` is modified, and no schema migration is added.
"""

from __future__ import annotations

import datetime as dt
import json
import logging
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, raiseload

from .db import (
    Approval,
    ReminderDelivery,
    Task,
    TaskEvent,
    TodoItem,
    User,
    utcnow,
)
from .engine import dependency_graph, lease_is_live, task_summary_to_dict
from .flow import approval_to_dict
from .reminders import (
    RemindersConfig,
    bound_data_dir,
    deliver_due_reminders,
    load_reminders_config,
)

logger = logging.getLogger(__name__)

# Lane predicates mirror routers/summary.py where the lanes overlap; keep the
# two read models in sync when the summary definitions move.
PENDING_APPROVALS_LIMIT = 100
REVIEW_EVENT_TYPES = ("review_comment", "review_reply")

DIGEST_CHANNEL = "in_app"
DIGEST_KEY_PREFIX = "inbox-digest"
# Viewers are refused on every todo route, so a digest anchored to a private
# todo would be undeliverable noise for them; owners are admin/member users.
DIGEST_USER_ROLES = ("admin", "member")


def _aware(value: dt.datetime | None) -> dt.datetime | None:
    if value is None:
        return None
    if value.tzinfo is None or value.utcoffset() is None:
        return value.replace(tzinfo=dt.timezone.utc)
    return value.astimezone(dt.timezone.utc)


def digest_anchor_id(user_id: int) -> str:
    """Deterministic digest-anchor todo id for one owner account."""
    return f"{DIGEST_KEY_PREFIX}-{int(user_id):06d}"


def digest_delivery_key(user_id: int, date_str: str) -> str:
    """Date-keyed idempotency key: one digest slot per owner per day."""
    return f"{DIGEST_KEY_PREFIX}:{int(user_id)}:{date_str}"


def _review_body(event: TaskEvent) -> str:
    try:
        payload = json.loads(event.payload_json or "{}")
    except json.JSONDecodeError:
        return ""
    return str(payload.get("body") or "")


def _pending_reviews(db: Session) -> list[dict[str, Any]]:
    """QC comments with no reply yet, newest first, across live cards."""
    rows = db.execute(
        select(TaskEvent, Task.title)
        .join(Task, Task.id == TaskEvent.task_id)
        .where(
            Task.archived.is_(False),
            TaskEvent.event_type.in_(REVIEW_EVENT_TYPES),
        )
        .order_by(TaskEvent.id)
    ).all()
    comments: dict[str, dict[str, Any]] = {}
    replied: set[str] = set()
    for event, title in rows:
        if event.event_type == "review_comment" and event.event_key:
            comments[event.event_key] = {
                "task_id": event.task_id,
                "task_title": title,
                "review_id": event.event_key,
                "author": event.who,
                "body": _review_body(event),
                "created_at": event.at,
            }
        elif event.event_type == "review_reply" and event.parent_key:
            replied.add(event.parent_key)
    pending = [item for key, item in comments.items() if key not in replied]
    pending.reverse()
    return pending


def collect_inbox(
    db: Session,
    *,
    now: dt.datetime | None = None,
    today: dt.date | None = None,
    lane_limit: int = 5,
) -> dict[str, Any]:
    """Fold the four attention lanes into one read model (read-only)."""
    clock = _aware(now) or utcnow()
    today_date = today or clock.date()

    tasks = list(
        db.execute(
            select(Task)
            .options(raiseload(Task.events), raiseload(Task.attempts))
            .where(Task.archived.is_(False))
            .order_by(Task.created_at.desc(), Task.id.desc())
        ).scalars()
    )
    graph = dependency_graph(db, {task.id for task in tasks})
    projections = {
        task.id: task_summary_to_dict(task, graph[task.id]) for task in tasks
    }

    pending_approvals = list(
        db.execute(
            select(Approval)
            .where(Approval.status == "pending")
            .order_by(Approval.created_at.desc())
            .limit(PENDING_APPROVALS_LIMIT)
        ).scalars()
    )
    decisions = [
        approval_to_dict(row, db.get(Task, row.task_id)) for row in pending_approvals
    ]

    reviews = _pending_reviews(db)

    blocked = [task for task in tasks if task.status == "blocked"]

    stale: list[dict[str, Any]] = []
    for task in tasks:
        if task.status != "doing":
            continue
        reasons: list[str] = []
        if task.due_at is not None and task.due_at < today_date:
            reasons.append("overdue")
        if int(task.lease_term or 0) > 0 and not lease_is_live(task, clock):
            reasons.append("heartbeat_lost")
        if reasons:
            stale.append({"task": projections[task.id], "reasons": reasons})

    return {
        "generated_at": clock.isoformat(),
        "today": today_date.isoformat(),
        "lanes": {
            "decisions": {
                "count": len(decisions),
                "items": decisions[:lane_limit],
            },
            "reviews": {
                "count": len(reviews),
                "items": reviews[:lane_limit],
            },
            "blocked": {
                "count": len(blocked),
                "items": [projections[task.id] for task in blocked[:lane_limit]],
            },
            "stale": {
                "count": len(stale),
                "items": stale[:lane_limit],
            },
        },
    }


def _digest_title(date_str: str, counts: dict[str, int]) -> str:
    return (
        f"收件箱日报 {date_str}:"
        f"待拍板 {counts.get('decisions', 0)} · "
        f"待质检 {counts.get('reviews', 0)} · "
        f"阻塞 {counts.get('blocked', 0)} · "
        f"超期未动 {counts.get('stale', 0)}"
    )[:256]


def ensure_daily_digests(
    db: Session,
    *,
    now: dt.datetime | None = None,
    data_dir: Path | str | None = None,
    config: RemindersConfig | None = None,
) -> dict[str, Any]:
    """Register today's digest slot for every owner account (idempotent).

    The whole facility follows the reminders config: when
    ``<data-dir>/reminders.yaml`` is absent or ``enabled: false`` no slots are
    registered at all, so disabled deployments never accumulate backlog.
    """
    cfg = config or load_reminders_config(
        data_dir if data_dir is not None else bound_data_dir()
    )
    clock = _aware(now) or utcnow()
    date_str = clock.date().isoformat()
    result: dict[str, Any] = {
        "date": date_str,
        "enabled": bool(cfg.enabled),
        "registered": 0,
        "owners": 0,
    }
    if not cfg.enabled:
        return result

    users = list(
        db.execute(
            select(User)
            .where(User.disabled.is_(False), User.role.in_(DIGEST_USER_ROLES))
            .order_by(User.id)
        ).scalars()
    )
    result["owners"] = len(users)
    if not users:
        return result

    lanes = collect_inbox(db, now=clock, lane_limit=1)["lanes"]
    counts = {name: int(lane["count"]) for name, lane in lanes.items()}
    title = _digest_title(date_str, counts)

    for user in users:
        key = digest_delivery_key(user.id, date_str)
        existing = db.execute(
            select(ReminderDelivery).where(ReminderDelivery.delivery_key == key)
        ).scalar_one_or_none()
        if existing is not None:
            continue
        anchor = db.get(TodoItem, digest_anchor_id(user.id))
        if anchor is None:
            anchor = TodoItem(
                id=digest_anchor_id(user.id),
                owner_user_id=user.id,
                title=title,
                status="open",
            )
            db.add(anchor)
            db.flush()
        else:
            anchor.title = title
        try:
            with db.begin_nested():
                db.add(
                    ReminderDelivery(
                        todo_item_id=anchor.id,
                        owner_user_id=user.id,
                        scheduled_for=clock,
                        channel=DIGEST_CHANNEL,
                        delivery_key=key,
                        status="pending",
                    )
                )
        except IntegrityError:
            # A concurrent scan already claimed today's slot for this owner.
            continue
        result["registered"] += 1
    db.flush()
    return result


def run_daily_digest(
    db: Session,
    *,
    now: dt.datetime | None = None,
    data_dir: Path | str | None = None,
    config: RemindersConfig | None = None,
    http_post=None,
) -> dict[str, Any]:
    """Ensure today's slots exist, then hand them to the reminders scanner.

    Safe to call repeatedly: registration dedupes on the date-keyed
    ``delivery_key`` and the scanner never re-sends a terminal slot, so a
    same-day rescan neither re-registers nor re-delivers.
    """
    base_dir = data_dir if data_dir is not None else bound_data_dir()
    cfg = config or load_reminders_config(base_dir)
    ensured = ensure_daily_digests(db, now=now, data_dir=base_dir, config=cfg)
    delivered = 0
    if cfg.enabled:
        outcome = deliver_due_reminders(
            db, now=now, data_dir=base_dir, config=cfg, http_post=http_post
        )
        delivered = int(outcome.get("count", 0))
    return {**ensured, "delivered": delivered}
