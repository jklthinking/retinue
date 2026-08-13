"""Explicit node-membership decisions shared by administrative workflows."""

from __future__ import annotations

from sqlalchemy.orm import Session

from .db import Node, utcnow


def admit_node(
    db: Session, *, node_id: str, label: str, admitted_by: str
) -> tuple[Node, bool]:
    """Apply one explicit admission while preserving existing telemetry history."""
    node = db.get(Node, node_id)
    if node is not None and node.membership_status == "admitted":
        return node, False
    now = utcnow()
    if node is None:
        node = Node(id=node_id)
        db.add(node)
    node.label = label or node.label or node_id
    node.membership_status = "admitted"
    node.admitted_by = admitted_by
    node.admitted_at = now
    return node, True
