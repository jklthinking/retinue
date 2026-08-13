"""Authentication routes: password login, demo login, logout, identity."""

from __future__ import annotations

import os
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db import User, WebSession, utcnow
from ..deps import (
    SESSION_COOKIE,
    SESSION_TTL,
    Principal,
    get_db,
    require_auth,
    session_cookie_secure,
    site_config,
)
from ..schemas import LoginBody
from ..security import hash_token, new_token, verify_password

router = APIRouter()


def login_source(request: Request) -> str:
    return request.client.host if request.client else "unknown"


def reject_throttled_login(retry_after: int) -> None:
    raise HTTPException(
        status_code=429,
        detail="用户名或密码错误",
        headers={"Retry-After": str(retry_after)},
    )


@router.post("/api/auth/login")
def login(
    body: LoginBody,
    request: Request,
    response: Response,
    db: Session = Depends(get_db, scope="function"),
) -> dict[str, Any]:
    source = login_source(request)
    # This gate must remain before both the user lookup and scrypt verification.
    retry_after = request.app.state.login_throttle.begin_attempt(body.username, source)
    if retry_after:
        reject_throttled_login(retry_after)
    try:
        user = db.execute(
            select(User).where(User.username == body.username)
        ).scalar()
        valid_password = bool(
            user
            and not user.disabled
            and verify_password(body.password, user.password_hash)
        )
    except Exception:
        request.app.state.login_throttle.cancel_attempt(body.username, source)
        raise
    if not valid_password:
        request.app.state.login_throttle.record_failure(body.username, source)
        raise HTTPException(status_code=401, detail="用户名或密码错误")
    request.app.state.login_throttle.record_success(body.username, source)
    token = new_token("rts")
    db.add(
        WebSession(
            token_hash=hash_token(token),
            user_id=user.id,
            expires_at=utcnow() + SESSION_TTL,
        )
    )
    response.set_cookie(
        SESSION_COOKIE,
        token,
        max_age=int(SESSION_TTL.total_seconds()),
        httponly=True,
        samesite="lax",
        secure=session_cookie_secure(request),
    )
    return {"username": user.username, "role": user.role, "display_name": user.display_name}


@router.post("/api/auth/demo-login")
def demo_login(
    request: Request,
    response: Response,
    db: Session = Depends(get_db, scope="function"),
) -> dict[str, Any]:
    """One-click login as the configured demo account. Only active when the
    operator has explicitly set demo_user in site-config.json."""
    retry_after = request.app.state.login_throttle.source_retry_after(
        login_source(request)
    )
    if retry_after:
        raise HTTPException(
            status_code=429,
            detail="登录请求过于频繁，请稍后重试",
            headers={"Retry-After": str(retry_after)},
        )
    demo_user = site_config(request.app.state.data_dir).get("demo_user")
    if not demo_user:
        raise HTTPException(status_code=404, detail="demo login is not enabled here")
    user = db.execute(select(User).where(User.username == demo_user)).scalar()
    if user is None or user.disabled:
        raise HTTPException(status_code=422, detail=f"demo user missing: {demo_user}")
    token = new_token("rts")
    db.add(
        WebSession(
            token_hash=hash_token(token),
            user_id=user.id,
            expires_at=utcnow() + SESSION_TTL,
        )
    )
    response.set_cookie(
        SESSION_COOKIE,
        token,
        max_age=int(SESSION_TTL.total_seconds()),
        httponly=True,
        samesite="lax",
        secure=session_cookie_secure(request),
    )
    return {"username": user.username, "role": user.role, "display_name": user.display_name}


@router.post("/api/auth/logout")
def logout(request: Request, response: Response, db: Session = Depends(get_db, scope="function")) -> dict[str, str]:
    cookie = request.cookies.get(SESSION_COOKIE)
    if cookie:
        record = db.execute(
            select(WebSession).where(WebSession.token_hash == hash_token(cookie))
        ).scalar()
        if record:
            db.delete(record)
    response.delete_cookie(SESSION_COOKIE)
    return {"status": "ok"}


@router.get("/api/auth/me")
def me(request: Request, principal: Principal = Depends(require_auth)) -> dict[str, Any]:
    data_dir = request.app.state.data_dir
    return {
        "kind": principal.kind,
        "name": principal.name,
        "role": principal.role,
        "actor_id": principal.actor_id,
        "display_name": principal.user.display_name if principal.user else principal.name,
        "site_console": bool(os.environ.get("RETINUE_INTERNAL_ROOT")),
        "mode": site_config(data_dir).get("mode", ""),
        "site_label": site_config(data_dir).get("label", ""),
        "readonly": principal.role == "viewer",
    }
