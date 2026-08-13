"""MCP stdio bridge: exposes Retinue Server task tools to any MCP runtime.

The bridge authenticates with one agent bearer token, so the server's
holder-only-writes boundary applies to every call. Configure via:

    RETINUE_SERVER_URL   e.g. http://127.0.0.1:9219
    RETINUE_TOKEN_FILE   path to a file holding the token (preferred: a configuration
                         file that names a path carries no secret)
    RETINUE_TOKEN        the token itself, for environments without a usable file

Claude Code registration example (.mcp.json):
    {"mcpServers": {"retinue": {
        "command": "/path/to/retinue/.venv/bin/python",
        "args": ["-m", "server.mcp_bridge"],
        "cwd": "/path/to/retinue",
        "env": {"RETINUE_SERVER_URL": "http://127.0.0.1:9219",
                 "RETINUE_TOKEN_FILE": "/path/to/agent.token"}}}}
"""

from __future__ import annotations

import json
import os
from pathlib import Path
import shlex
import stat
import urllib.error
import urllib.request
from typing import Any, Literal

try:
    from mcp.server.fastmcp import FastMCP
except ModuleNotFoundError as exc:  # the mcp extra is not installed
    if not (exc.name or "").startswith("mcp"):
        raise
    FastMCP = None

from .http_client import RequestClass, open_url

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
AttemptOutcome = Literal["succeeded", "failed", "cancelled"]


class BridgeError(RuntimeError):
    pass


def _uses_unix_file_permissions() -> bool:
    return os.name == "posix"


def _token() -> str:
    """The bearer token, preferring a file so a configuration file need not hold it.

    A runtime's MCP configuration is often inside a project directory, so a raw token
    placed there can be committed. RETINUE_TOKEN_FILE keeps the credential in a file the
    operator controls, matching RETINUE_NODE_TOKEN_FILE and RETINUE_ACTOR_TOKEN_FILE.
    """
    path = os.environ.get("RETINUE_TOKEN_FILE", "").strip()
    if path:
        token_file = Path(path)
        try:
            with token_file.open(encoding="utf-8") as stream:
                # Windows ACLs are not represented by these mode bits, so applying
                # the Unix check there would reject safely configured agent files.
                if _uses_unix_file_permissions():
                    mode = stat.S_IMODE(os.fstat(stream.fileno()).st_mode)
                    if mode & (stat.S_IRGRP | stat.S_IROTH):
                        command_path = shlex.quote(str(token_file))
                        raise BridgeError(
                            f"RETINUE_TOKEN_FILE {token_file} has insecure permissions "
                            f"{mode:03o}; group or other users can read it; "
                            f"run: chmod 600 {command_path}"
                        )
                token = stream.read().strip()
        except OSError as exc:
            raise BridgeError(f"cannot read RETINUE_TOKEN_FILE: {exc.strerror}") from exc
        if not token:
            raise BridgeError("RETINUE_TOKEN_FILE is empty")
        return token
    return os.environ.get("RETINUE_TOKEN", "").strip()


def _call(method: str, path: str, body: dict[str, Any] | None = None) -> Any:
    base = os.environ.get("RETINUE_SERVER_URL", "").rstrip("/")
    token = _token()
    if not base or not token:
        raise BridgeError(
            "set RETINUE_SERVER_URL, and either RETINUE_TOKEN_FILE (preferred) or "
            "RETINUE_TOKEN"
        )
    request = urllib.request.Request(
        base + path,
        method=method,
        data=json.dumps(body).encode("utf-8") if body is not None else None,
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        },
    )
    try:
        with open_url(request, timeout=15, request_class=RequestClass.INWARD) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        try:
            detail = json.loads(exc.read().decode("utf-8")).get("detail", str(exc))
        except Exception:
            detail = str(exc)
        raise BridgeError(f"{exc.code}: {detail}") from exc
    except urllib.error.URLError as exc:
        raise BridgeError(f"cannot reach Retinue Server: {exc.reason}") from exc


def _summary(task: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": task["id"],
        "title": task["title"],
        "status": task["status"],
        "holder": task["holder"],
        "priority": task.get("priority"),
        "progress": task.get("progress", 0),
        "open_dispatch": task.get("open_dispatch", False),
        "blocked_reason": task.get("blocked_reason"),
        "blocked_by": task.get("blocked_by", []),
        "blocks": task.get("blocks", []),
        "ready": task.get("ready", False),
        "last_receipt_at": task["chain"][-1]["at"] if task.get("chain") else None,
        "receipt": task.get("receipt"),
        "pipeline_stage": task.get("pipeline_stage"),
        "current_stage": (
            task.get("pipeline", [])[task.get("pipeline_stage", 0)]
            if task.get("pipeline")
            else None
        ),
        "created": task.get("created"),
        "idempotency_key": task.get("idempotency_key"),
        "matched_template": task.get("matched_template"),
        "matched_terms": task.get("matched_terms"),
    }


def create_server() -> FastMCP:
    _require_mcp()
    server = FastMCP(
        "Retinue",
        instructions=(
            "Coordinate through Retinue task cards. Read before writing. States: "
            "queued -> doing -> done or handoff; doing may also become blocked; "
            "handoff/blocked return to doing. You may only mutate cards you hold. "
            "Every status or holder transition needs a receipt-quality note. "
            "Report each completed execution with task_attempt; this records an "
            "outcome without changing task state. "
            "For natural-language intake, dispatch_intent requires the stable source "
            "message ID as idempotency_key."
        ),
        json_response=True,
    )

    @server.tool(name="whoami")
    def whoami() -> dict[str, Any]:
        """Show which Retinue actor this bridge writes as."""
        return _call("GET", "/api/auth/me")

    @server.tool(name="task_list")
    def task_list(status: TaskStatus | None = None, holder: str | None = None) -> list[dict[str, Any]]:
        """List task cards, optionally filtered by status and/or holder."""
        query = []
        if status:
            query.append(f"status={status}")
        if holder:
            query.append(f"holder={holder}")
        path = "/api/tasks" + ("?" + "&".join(query) if query else "")
        return [_summary(task) for task in _call("GET", path)]

    @server.tool(name="my_tasks")
    def my_tasks(include_terminal: bool = False) -> list[dict[str, Any]]:
        """List cards currently held by this agent."""
        me = _call("GET", "/api/auth/me")
        tasks = _call("GET", f"/api/tasks?holder={me['actor_id']}")
        if not include_terminal:
            tasks = [t for t in tasks if t["status"] not in ("done", "cancelled")]
        return [_summary(task) for task in tasks]

    @server.tool(name="ready_work")
    def ready_work(holder: str | None = None) -> list[dict[str, Any]]:
        """List queued cards whose prerequisite cards are all done."""
        path = "/api/tasks/ready"
        if holder:
            path += f"?holder={holder}"
        return [_summary(task) for task in _call("GET", path)]

    @server.tool(name="task_show")
    def task_show(task_id: str) -> dict[str, Any]:
        """Read one full task card including its event chain and acceptance."""
        return _call("GET", f"/api/tasks/{task_id}")

    @server.tool(name="open_tasks")
    def open_tasks() -> list[dict[str, Any]]:
        """List cards on the dispatch hall waiting to be claimed."""
        return [
            _summary(task)
            for task in _call("GET", "/api/tasks/ready")
            if task.get("open_dispatch")
        ]

    @server.tool(name="task_claim")
    def task_claim(task_id: str, note: str = "接单") -> dict[str, Any]:
        """Claim an open card from the dispatch hall; the baton moves to you."""
        return _summary(_call("POST", f"/api/tasks/{task_id}/claim", {"note": note}))

    @server.tool(name="dispatch_intent")
    def dispatch(
        intent: str,
        idempotency_key: str,
        template_name: str | None = None,
        priority: Priority = "none",
        acceptance: list[str] | None = None,
    ) -> dict[str, Any]:
        """Create exactly one pipeline card from a natural-language source event.

        Use the source platform's immutable message/event ID as idempotency_key.
        Replaying the same event returns the original card without creating another.
        """
        return _summary(
            _call(
                "POST",
                "/api/dispatch",
                {
                    "intent": intent,
                    "idempotency_key": idempotency_key,
                    "template_name": template_name,
                    "priority": priority,
                    "acceptance": acceptance or [],
                },
            )
        )

    @server.tool(name="task_dependency_add")
    def task_dependency_add(
        task_id: str,
        prerequisite_id: str,
        note: str = "dependency added",
    ) -> dict[str, Any]:
        """Make a queued card wait for another card to finish."""
        result = _call(
            "POST",
            f"/api/tasks/{task_id}/dependencies",
            {
                "prerequisite_id": prerequisite_id,
                "kind": "blocks",
                "note": note,
            },
        )
        return _summary(result["task"])

    @server.tool(name="task_dependency_remove")
    def task_dependency_remove(
        task_id: str,
        prerequisite_id: str,
        note: str = "dependency removed",
    ) -> dict[str, Any]:
        """Remove one finish-to-start prerequisite from a queued card."""
        result = _call(
            "DELETE",
            f"/api/tasks/{task_id}/dependencies/{prerequisite_id}",
            {"note": note},
        )
        return _summary(result["task"])

    @server.tool(name="task_start")
    def task_start(task_id: str, note: str = "接棒开工") -> dict[str, Any]:
        """Start or resume the current pipeline stage on a card you hold."""
        return _summary(
            _call("POST", f"/api/tasks/{task_id}/update", {"status": "doing", "note": note})
        )

    @server.tool(name="task_progress")
    def task_progress(task_id: str, percent: int, note: str) -> dict[str, Any]:
        """Report progress (0-100) on a card you hold, with a receipt note."""
        return _summary(
            _call("POST", f"/api/tasks/{task_id}/update", {"progress": percent, "note": note})
        )

    @server.tool(name="task_new")
    def task_new(
        title: str,
        holder: str | None = None,
        dept: str | None = None,
        priority: Priority = "none",
        acceptance: list[str] | None = None,
        depends_on: list[str] | None = None,
        due_at: str | None = None,
        note: str = "task created",
        open_dispatch: bool = False,
    ) -> dict[str, Any]:
        """Create a queued card. Give `holder` to assign directly, or set
        open_dispatch=true to post it on the dispatch hall for claiming.
        `due_at` is a calendar-day deadline in YYYY-MM-DD form."""
        return _summary(
            _call(
                "POST",
                "/api/tasks",
                {
                    "title": title,
                    "holder": holder,
                    "dept": dept,
                    "priority": priority,
                    "acceptance": acceptance or [],
                    "depends_on": depends_on or [],
                    "due_at": due_at,
                    "note": note,
                    "open_dispatch": open_dispatch,
                },
            )
        )

    @server.tool(name="task_update")
    def task_update(
        task_id: str,
        note: str,
        status: TaskStatus | None = None,
        holder: str | None = None,
        blocked_reason: str | None = None,
        refs: list[str] | None = None,
    ) -> dict[str, Any]:
        """Transition or annotate a card you hold. `note` becomes the receipt."""
        body: dict[str, Any] = {"note": note}
        if status:
            body["status"] = status
        if holder:
            body["holder"] = holder
        if blocked_reason:
            body["blocked_reason"] = blocked_reason
        if refs:
            body["refs"] = refs
        return _summary(_call("POST", f"/api/tasks/{task_id}/update", body))

    @server.tool(name="task_attempt")
    def task_attempt(
        task_id: str,
        outcome: AttemptOutcome,
        started_at: str,
        ended_at: str,
        idempotency_key: str,
        reason: str | None = None,
        exit_status: int | None = None,
    ) -> dict[str, Any]:
        """Record one completed execution attempt on a card you currently hold.

        A failure needs a short, sanitized reason and may include an exit status.
        Never put a transcript, command line, credential, or path in the reason.
        Recording the outcome does not transition or otherwise update the card.
        """
        return _call(
            "POST",
            f"/api/tasks/{task_id}/attempts",
            {
                "outcome": outcome,
                "started_at": started_at,
                "ended_at": ended_at,
                "idempotency_key": idempotency_key,
                "reason": reason,
                "exit_status": exit_status,
            },
        )

    @server.tool(name="stage_done")
    def stage_done(task_id: str, note: str, confidence: float | None = None) -> dict[str, Any]:
        """Finish your pipeline node and pass the baton (queen gates open a
        decision card automatically). Requires the card to be doing."""
        body: dict[str, Any] = {"note": note}
        if confidence is not None:
            body["confidence"] = confidence
        return _summary(_call("POST", f"/api/tasks/{task_id}/stage-done", body))

    @server.tool(name="stage_reject")
    def stage_reject(task_id: str, note: str) -> dict[str, Any]:
        """On a review node: send the baton back to the previous stage."""
        return _summary(_call("POST", f"/api/tasks/{task_id}/stage-reject", {"note": note}))

    @server.tool(name="task_receipt")
    def task_receipt(task_id: str) -> str:
        """Render the latest event of a card as a two-line IM receipt."""
        task = _call("GET", f"/api/tasks/{task_id}")
        if not task.get("chain"):
            return f"【任务回执】{task['id']} {task['title']}(暂无事件)"
        event = task["chain"][-1]
        return (
            f"【任务回执】{task['id']} {task['title']}\n"
            f"状态:{event.get('from_status') or '—'} → {event.get('to_status') or task['status']}　"
            f"持棒:{event.get('from_holder') or '—'} → {event.get('to_holder') or task['holder']}　"
            f"备注:{event['did']}"
        )

    return server


def main() -> None:
    create_server().run()


if __name__ == "__main__":
    main()
