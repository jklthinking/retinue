"""FastAPI application: auth, actors, task board, metrics, admin.

Composition only: this module wires application state into ``app.state`` and
includes the domain routers from ``server.routers``. Route bodies live with
the thing they serve; shared dependencies live in ``server.deps`` and
``server.helpers``. Router include order matches the historical registration
order of the single-file application, and the panel mount stays last, because
route ordering is behaviour.
"""

from __future__ import annotations

import os
from pathlib import Path

from fastapi import Depends, FastAPI
from sqlalchemy.orm import sessionmaker

from . import __version__
from .deps import require_admin
from .panel import mount_panel
from .routers import (
    actors,
    admin,
    agent_card,
    approvals,
    auth,
    enroll,
    inbox,
    intake,
    distill,
    knowledge,
    live_sessions,
    metrics,
    nodes,
    orientation,
    sessions,
    skills,
    status,
    summary,
    tasks,
    templates,
    todos,
    card_pipelines,
    collaboration,
)
from .security import LoginThrottle, verify_password  # noqa: F401 — re-exported:
# tests patch ``server.app.verify_password`` to observe the login hash path.

# Registration order matches the original single-closure module, so the route
# table (including resolution order for overlapping shapes) is unchanged.
_ROUTERS = (
    orientation.router,
    status.router,
    summary.router,
    inbox.router,
    auth.router,
    actors.router,
    tasks.router,
    collaboration.router,
    approvals.router,
    templates.router,
    card_pipelines.router,
    metrics.router,
    sessions.router,
    live_sessions.router,
    todos.router,
    admin.router,
    skills.router,
    nodes.router,
    knowledge.router,
    distill.router,
    agent_card.router,
    intake.router,
    enroll.router,
)


def create_app(
    session_factory: sessionmaker,
    static_dir: Path | None = None,
    data_dir: Path | None = None,
) -> FastAPI:
    app = FastAPI(title="Retinue Server", version=__version__)
    app.state.session_factory = session_factory
    app.state.data_dir = data_dir
    app.state.login_throttle = LoginThrottle()

    from .reminders import bind_data_dir
    from .notify import bind_data_dir as bind_notify_data_dir
    from .qc_hook import bind_data_dir as bind_qc_hook_data_dir

    bind_data_dir(data_dir)
    bind_notify_data_dir(data_dir)
    bind_qc_hook_data_dir(data_dir)

    for router in _ROUTERS:
        app.include_router(router)

    # ---------- kingdom console (optional module, extracted from the old
    # hermes panel; reads redacted observer snapshots, admin-only) ----------

    if os.environ.get("RETINUE_KINGDOM_ROOT"):
        from .kingdom import router as kingdom_router

        app.include_router(kingdom_router, dependencies=[Depends(require_admin)])

    # ---------- static SPA (must stay last: the catch-all shadows nothing) ----------

    mount_panel(app, static_dir)

    return app
