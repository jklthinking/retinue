"""Panel static mounting and the SPA fallback.

Kept as the last wiring step in ``create_app``: the ``/assets`` mount and the
``/{path:path}`` catch-all must be registered after every API route, because
route ordering is behaviour. ``mount_panel`` is therefore called last, and
only here.
"""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles


def mount_panel(app: FastAPI, static_dir: Path | None) -> None:
    if not (static_dir and static_dir.is_dir()):
        return
    app.mount("/assets", StaticFiles(directory=static_dir / "assets"), name="assets")

    @app.get("/{path:path}", include_in_schema=False)
    def spa(path: str) -> FileResponse:
        if path.startswith("api/"):
            raise HTTPException(status_code=404, detail="unknown API path")
        candidate = (static_dir / path).resolve()
        if (
            path
            and candidate.is_file()
            and candidate.is_relative_to(static_dir.resolve())
        ):
            return FileResponse(candidate)
        return FileResponse(static_dir / "index.html")
