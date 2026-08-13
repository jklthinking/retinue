"""Orientation read models: the context packet and the data catalog."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from ..deps import Principal, get_db, require_auth
from ..helpers import build_data_catalog, build_orientation_context

router = APIRouter()


@router.get("/api/orientation/context")
def orientation_context(
    principal: Principal = Depends(require_auth),
    db: Session = Depends(get_db, scope="function"),
) -> dict[str, Any]:
    """Return the current safe orientation packet for this caller."""
    return build_orientation_context(db, principal)


@router.get("/api/data-catalog")
def data_catalog(
    principal: Principal = Depends(require_auth),
    db: Session = Depends(get_db, scope="function"),
) -> dict[str, Any]:
    """Return the storage-layer catalog and quality checks for the workbench."""
    return build_data_catalog(db)
