"""MCP onboarding surface for Retinue task coordination."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Literal

try:
    from mcp.server.fastmcp import FastMCP
except ModuleNotFoundError as exc:  # the mcp extra is not installed
    if not (exc.name or "").startswith("mcp"):
        raise
    FastMCP = None

from core.protocol.task import (
    ProtocolError,
    add_dependency,
    create_task,
    load_task,
    ready_tasks,
    remove_dependency,
    render_receipt,
    update_task,
)

_MCP_MISSING = (
    "MCP support is not installed; it lives in the 'mcp' extra: "
    "pip install 'retinue[mcp]'"
)


def _require_mcp() -> None:
    """Refuse helpfully, not with an ImportError traceback, when the
    deployment does not carry the mcp extra (a base or node install)."""
    if FastMCP is None:
        raise SystemExit(_MCP_MISSING)


TaskStatus = Literal["queued", "doing", "handoff", "blocked", "done", "cancelled"]
Priority = Literal["urgent", "high", "medium", "low", "none"]


def _task_path(root: Path, task_id: str) -> Path:
    """Resolve a task id without permitting paths outside the canonical bus."""
    if "/" in task_id or "\\" in task_id or task_id in {".", ".."}:
        raise ProtocolError("task_id must be a task id, not a path")
    path = root / "tasks" / f"{task_id}.yaml"
    if not path.is_file():
        raise ProtocolError(f"unknown task: {task_id}")
    return path


def _cards(root: Path) -> dict[str, dict[str, Any]]:
    return {
        card["id"]: card
        for path in sorted((root / "tasks").glob("*.y*ml"))
        for card in [load_task(path)]
    }


def _summary(
    task: dict[str, Any], cards: dict[str, dict[str, Any]] | None = None
) -> dict[str, Any]:
    cards = cards or {task["id"]: task}
    blocked_by = [
        {
            "id": task_id,
            "title": cards[task_id]["title"],
            "status": cards[task_id]["status"],
            "kind": "blocks",
        }
        for task_id in task.get("depends_on", [])
        if task_id in cards
    ]
    blocks = [
        {
            "id": candidate["id"],
            "title": candidate["title"],
            "status": candidate["status"],
            "kind": "blocks",
        }
        for candidate in cards.values()
        if task["id"] in candidate.get("depends_on", [])
    ]
    return {
        "id": task["id"],
        "title": task["title"],
        "status": task["status"],
        "holder": task["holder"],
        "priority": task.get("priority"),
        "blocked_by": blocked_by,
        "blocks": blocks,
        "ready": task["status"] == "queued"
        and all(item["status"] == "done" for item in blocked_by),
        "last_receipt_at": task["chain"][-1]["at"] if task["chain"] else None,
    }


def create_server(root: Path | str, agent_id: str | None = None) -> FastMCP:
    """Create one stdio-safe server bound to a single Retinue workspace."""
    _require_mcp()
    workspace = Path(root).resolve()
    identity = agent_id or os.environ.get("RETINUE_AGENT_ID")
    if not (workspace / "org.yaml").is_file():
        raise ProtocolError(f"Retinue workspace has no org.yaml: {workspace}")

    server = FastMCP(
        "Retinue",
        instructions=(
            "Coordinate through canonical task cards. Read before writing. States: "
            "queued -> doing -> done or handoff; doing may also become blocked; "
            "handoff/blocked return to doing. Only the holder may mutate a card. "
            "Every status or holder transition needs a receipt-quality note."
        ),
        json_response=True,
    )

    @server.tool(name="task_list")
    def task_list(status: TaskStatus | None = None, holder: str | None = None) -> list[dict[str, Any]]:
        """List task cards, optionally filtered by status and/or holder."""
        tasks: list[dict[str, Any]] = []
        cards = _cards(workspace)
        for task in cards.values():
            if status is not None and task["status"] != status:
                continue
            if holder is not None and task["holder"] != holder:
                continue
            tasks.append(_summary(task, cards))
        return tasks

    @server.tool(name="ready_work")
    def ready_work(holder: str | None = None) -> list[dict[str, Any]]:
        """List queued cards whose prerequisite cards are all done."""
        cards = _cards(workspace)
        return [
            _summary(task, cards)
            for task in ready_tasks(workspace / "tasks")
            if holder is None or task["holder"] == holder
        ]

    @server.tool(name="my_tasks")
    def my_tasks(include_terminal: bool = False) -> list[dict[str, Any]]:
        """List cards held by this MCP server's configured agent identity."""
        if not identity:
            raise ProtocolError("my_tasks requires --agent or RETINUE_AGENT_ID")
        terminal = {"done", "cancelled"}
        return [
            item
            for item in task_list(holder=identity)
            if include_terminal or item["status"] not in terminal
        ]

    @server.tool(name="task_new")
    def task_new(
        task_id: str,
        title: str,
        holder: str | None = None,
        dept: str | None = None,
        priority: Priority = "none",
        acceptance: list[str] | None = None,
        depends_on: list[str] | None = None,
        note: str = "task created through MCP",
    ) -> dict[str, Any]:
        """Create a queued card. The configured agent is recorded as creator."""
        if not identity:
            raise ProtocolError("task_new requires --agent or RETINUE_AGENT_ID")
        path = create_task(
            workspace / "tasks",
            task_id=task_id,
            title=title,
            created_by=identity,
            holder=holder or identity,
            dept=dept,
            priority=priority,
            acceptance=acceptance or (),
            depends_on=depends_on or (),
            note=note,
        )
        task = load_task(path)
        cards = _cards(workspace)
        return {**_summary(task, cards), "receipt": render_receipt(task)}

    @server.tool(name="task_dependency_add")
    def task_dependency_add(
        task_id: str,
        prerequisite_id: str,
        note: str = "dependency added",
    ) -> dict[str, Any]:
        """Make a held queued card wait for another card to finish."""
        if not identity:
            raise ProtocolError("task_dependency_add requires an agent identity")
        path = _task_path(workspace, task_id)
        current = load_task(path)
        if current["holder"] != identity:
            raise ProtocolError(
                f"holder-only-writes: {identity} cannot update card held by {current['holder']}"
            )
        task = add_dependency(
            path, prerequisite_id, note=note, who=identity
        )
        return _summary(task, _cards(workspace))

    @server.tool(name="task_dependency_remove")
    def task_dependency_remove(
        task_id: str,
        prerequisite_id: str,
        note: str = "dependency removed",
    ) -> dict[str, Any]:
        """Remove one finish-to-start prerequisite from a held queued card."""
        if not identity:
            raise ProtocolError("task_dependency_remove requires an agent identity")
        path = _task_path(workspace, task_id)
        current = load_task(path)
        if current["holder"] != identity:
            raise ProtocolError(
                f"holder-only-writes: {identity} cannot update card held by {current['holder']}"
            )
        task = remove_dependency(path, prerequisite_id, note=note, who=identity)
        return _summary(task, _cards(workspace))

    @server.tool(name="task_update")
    def task_update(
        task_id: str,
        status: TaskStatus | None = None,
        holder: str | None = None,
        note: str | None = None,
        blocked_reason: str | None = None,
        next_holder: str | None = None,
        priority: Priority | None = None,
        acceptance: list[str] | None = None,
        refs: list[str] | None = None,
    ) -> dict[str, Any]:
        """Update a held card and return its canonical receipt."""
        if not identity:
            raise ProtocolError("task_update requires --agent or RETINUE_AGENT_ID")
        path = _task_path(workspace, task_id)
        current = load_task(path)
        if current["holder"] != identity:
            raise ProtocolError(
                f"holder-only-writes: {identity} cannot update card held by {current['holder']}"
            )
        task = update_task(
            path,
            status=status,
            holder=holder,
            blocked_reason=blocked_reason,
            next_holder=next_holder,
            priority=priority,
            acceptance=acceptance,
            refs=refs or (),
            note=note,
            who=identity,
        )
        return {
            **_summary(task, _cards(workspace)),
            "receipt": render_receipt(task),
        }

    @server.tool(name="task_receipt")
    def task_receipt(task_id: str) -> dict[str, str]:
        """Render the latest canonical two-line receipt for a card."""
        task = load_task(_task_path(workspace, task_id))
        return {"task_id": task_id, "receipt": render_receipt(task)}

    return server


def serve(root: Path | str, agent_id: str | None = None) -> None:
    """Run a Retinue MCP server over the local stdio transport."""
    create_server(root, agent_id).run(transport="stdio")
