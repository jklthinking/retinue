"""MCP directory listings require explicit tool annotation hints."""

import anyio
import yaml
from mcp.shared.memory import create_connected_server_and_client_session

from core.mcp_server import create_server
from server.mcp_bridge import create_server as create_bridge_server

_HINT_FIELDS = (
    "readOnlyHint",
    "destructiveHint",
    "idempotentHint",
    "openWorldHint",
)


def _assert_complete_hints(tools: list) -> None:
    for tool in tools:
        assert tool.annotations is not None, f"{tool.name} missing annotations"
        for field in _HINT_FIELDS:
            value = getattr(tool.annotations, field)
            assert isinstance(value, bool), f"{tool.name}.{field} must be bool, got {value!r}"


def _workspace(tmp_path):
    root = tmp_path / "fleet"
    (root / "tasks").mkdir(parents=True)
    (root / "org.yaml").write_text(
        yaml.safe_dump(
            {
                "org": "acme-inc",
                "departments": [{"id": "eng", "name": "Engineering"}],
                "agents": [
                    {"id": "coder-1", "dept": "eng", "runtime": "codex", "node": "laptop"}
                ],
                "nodes": [{"id": "laptop"}],
            }
        ),
        encoding="utf-8",
    )
    return root


def test_workspace_mcp_tools_declare_annotation_hints(tmp_path):
    async def scenario():
        server = create_server(_workspace(tmp_path), "coder-1")
        async with create_connected_server_and_client_session(server) as session:
            _assert_complete_hints((await session.list_tools()).tools)

    anyio.run(scenario)


def test_bridge_mcp_tools_declare_annotation_hints():
    async def scenario():
        server = create_bridge_server()
        async with create_connected_server_and_client_session(server) as session:
            tools = (await session.list_tools()).tools
            assert len(tools) == 32
            _assert_complete_hints(tools)

    anyio.run(scenario)
