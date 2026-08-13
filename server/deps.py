"""Shared HTTP dependencies: sessions, principals, auth gates, error mapping.

These used to be closures inside ``create_app``; that lexical capture is what
made the application unsplittable. They now live here once, and every router
imports them. Anything that genuinely needs application state (the session
factory, the data directory) reaches it explicitly through ``app.state``,
which ``create_app`` wires in exactly one place.
"""

from __future__ import annotations

import datetime as dt
import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterator

from fastapi import Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from core.protocol.task import ProtocolError

from .db import ApiToken, Node, NodeToken, User, WebSession, utcnow
from .engine import Conflict, Forbidden
from .security import hash_token

SESSION_COOKIE = "retinue_session"
SESSION_TTL = dt.timedelta(days=14)
ONLINE_WINDOW = dt.timedelta(minutes=15)


@dataclass
class Principal:
    kind: str  # "user" | "agent"
    name: str  # username or actor id
    actor_id: str | None
    role: str  # admin | member | viewer | agent
    user: User | None = None

    @property
    def privileged(self) -> bool:
        return self.role in ("admin", "member")

    @property
    def write_identity(self) -> str:
        """The protocol actor slug this principal writes chain events as."""
        return self.actor_id or self.name


def get_db(request: Request) -> Iterator[Session]:
    db = request.app.state.session_factory()
    try:
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def api_token_expired(record: ApiToken) -> bool:
    """Whether a stored agent credential has passed its optional expiry."""
    if record.expires_at is None:
        return False
    return record.expires_at.replace(tzinfo=dt.timezone.utc) <= utcnow()


def session_cookie_secure(request: Request) -> bool:
    """Whether the session cookie should carry the Secure attribute.

    RETINUE_COOKIE_SECURE forces the answer either way. Without it, the
    attribute follows how this request actually arrived: direct TLS, or a
    TLS-terminating proxy announcing itself via x-forwarded-proto. A plain
    HTTP deployment keeps a working cookie either way, and an HTTPS one no
    longer sends the session token over clear text.
    """
    forced = os.environ.get("RETINUE_COOKIE_SECURE", "").strip().lower()
    if forced in {"1", "true", "yes", "on"}:
        return True
    if forced in {"0", "false", "no", "off"}:
        return False
    if request.url.scheme == "https":
        return True
    forwarded = request.headers.get("x-forwarded-proto", "")
    return forwarded.split(",")[0].strip().lower() == "https"


def authenticate(request: Request, db: Session) -> Principal | None:
    header = request.headers.get("authorization", "")
    if header.lower().startswith("bearer "):
        token = header[7:].strip()
        record = db.execute(
            select(ApiToken).where(ApiToken.token_hash == hash_token(token))
        ).scalar()
        if (
            record
            and not record.disabled
            and not record.actor.disabled
            and not api_token_expired(record)
        ):
            record.last_used_at = utcnow()
            record.actor.last_seen_at = utcnow()
            return Principal(
                kind="agent",
                name=record.actor_id,
                actor_id=record.actor_id,
                role="agent",
            )
        return None
    cookie = request.cookies.get(SESSION_COOKIE)
    if cookie:
        record = db.execute(
            select(WebSession).where(WebSession.token_hash == hash_token(cookie))
        ).scalar()
        if record and record.expires_at.replace(tzinfo=dt.timezone.utc) > utcnow():
            user = record.user
            if user and not user.disabled:
                return Principal(
                    kind="user",
                    name=user.username,
                    actor_id=user.actor_id,
                    role=user.role,
                    user=user,
                )
    return None


def require_auth(request: Request, db: Session = Depends(get_db, scope="function")) -> Principal:
    principal = authenticate(request, db)
    if principal is None:
        raise HTTPException(status_code=401, detail="authentication required")
    if principal.role == "viewer" and request.method not in {
        "GET", "HEAD", "OPTIONS"
    }:
        raise HTTPException(
            status_code=403, detail="观察席为只读账号，不能修改实盘数据"
        )
    return principal


def require_admin(principal: Principal = Depends(require_auth)) -> Principal:
    if principal.role != "admin":
        raise HTTPException(status_code=403, detail="admin role required")
    return principal


def require_node_heartbeat(
    request: Request, node_id: str, db: Session
) -> None:
    """Require an admitted node's credential for node-attributed telemetry."""
    require_node_credential(request, node_id, db)


def require_node_credential(request: Request, node_id: str, db: Session) -> None:
    """Require a token bound to exactly one explicitly admitted node.

    Agent credentials and operator sessions are identities, not authority to
    forge node-attributed telemetry. Operators instead use the deliberate
    admission, retirement, and node-token administration routes.
    """
    header = request.headers.get("authorization", "")
    if not header.lower().startswith("bearer "):
        if authenticate(request, db) is not None:
            raise HTTPException(
                status_code=403, detail="node-scoped credential required"
            )
        raise HTTPException(status_code=401, detail="node authentication required")
    token = header[7:].strip()
    record = db.execute(
        select(NodeToken).where(NodeToken.token_hash == hash_token(token))
    ).scalar()
    if record is None:
        if authenticate(request, db) is not None:
            raise HTTPException(
                status_code=403, detail="node-scoped credential required"
            )
        raise HTTPException(status_code=401, detail="node authentication required")
    if record.disabled:
        raise HTTPException(status_code=401, detail="node token disabled")
    if record.node_id != node_id:
        raise HTTPException(status_code=403, detail="node token belongs to another node")
    node = db.get(Node, node_id)
    if node is None or node.membership_status != "admitted":
        raise HTTPException(
            status_code=403,
            detail=(
                f"node is not admitted: {node_id}; ask an administrator to admit it"
            ),
        )
    record.last_used_at = utcnow()


def wrap_protocol_errors(exc: ProtocolError) -> HTTPException:
    if isinstance(exc, Forbidden):
        code = 403
    elif isinstance(exc, Conflict):
        code = 409
    else:
        code = 422
    return HTTPException(status_code=code, detail=str(exc))


def site_config(data_dir: Path | None) -> dict[str, Any]:
    """Optional multi-site login switcher; data-dir/site-config.json:
    {"label": "...", "demo_user": "...", "sites": [{"label": "...", "url": "..."}]}"""
    if not data_dir:
        return {}
    path = Path(data_dir) / "site-config.json"
    if not path.is_file():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
