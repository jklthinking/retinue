"""FastAPI application: auth, actors, task board, metrics, admin.

Composition only: this module wires application state into ``app.state`` and
includes the domain routers from ``server.routers``. Route bodies live with
the thing they serve; shared dependencies live in ``server.deps`` and
``server.helpers``. Router include order matches the historical registration
order of the single-file application, and the panel mount stays last, because
route ordering is behaviour.
"""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from sqlalchemy.orm import sessionmaker

from . import __version__
from .panel import mount_panel
from .routers import (
    actors,
    admin,
    agent_card,
    approvals,
    auth,
    enroll,
    intake,
    knowledge,
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
)
from .security import LoginThrottle, verify_password  # noqa: F401 — re-exported:
# tests patch ``server.app.verify_password`` to observe the login hash path.

# Registration order matches the original single-closure module, so the route
# table (including resolution order for overlapping shapes) is unchanged.
_ROUTERS = (
    orientation.router,
    status.router,
    summary.router,
    auth.router,
    actors.router,
    tasks.router,
    approvals.router,
    templates.router,
    card_pipelines.router,
    metrics.router,
    sessions.router,
    todos.router,
    admin.router,
    skills.router,
    nodes.router,
    knowledge.router,
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

    for router in _ROUTERS:
        app.include_router(router)

    # ---------- static SPA (must stay last: the catch-all shadows nothing) ----------

    mount_panel(app, static_dir)

    return app
