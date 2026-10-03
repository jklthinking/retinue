from __future__ import annotations

import anyio
from mcp.shared.memory import create_connected_server_and_client_session

from server import mcp_bridge


def test_live_mcp_tools_share_hub_control_contract(monkeypatch):
    calls = []

    def fake_call(method, path, body=None):
        calls.append((method, path, body))
        if path == "/api/live-sessions":
            return [{"bound_live_session_id": "live-20260902-001"}]
        return {"id": "ctl-" + "a" * 24, "status": "queued"}

    monkeypatch.setattr(mcp_bridge, "_call", fake_call)

    async def scenario():
        server = mcp_bridge.create_server()
        async with create_connected_server_and_client_session(server) as session:
            names = {tool.name for tool in (await session.list_tools()).tools}
            assert {
                "live_sessions",
                "live_tell",
                "live_peek",
                "live_interrupt",
                "live_control_status",
            } <= names
            listed = await session.call_tool("live_sessions", {})
            assert not listed.isError
            sent = await session.call_tool(
                "live_tell",
                {
                    "live_session_id": "live-20260902-001",
                    "message": "status please",
                    "idempotency_key": "source:event-2001",
                },
            )
            assert not sent.isError

    anyio.run(scenario)
    assert calls == [
        ("GET", "/api/live-sessions", None),
        (
            "POST",
            "/api/live-sessions/live-20260902-001/control",
            {
                "verb": "tell",
                "message": "status please",
                "idempotency_key": "source:event-2001",
            },
        ),
    ]
