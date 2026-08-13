import anyio
import yaml
from mcp.shared.memory import create_connected_server_and_client_session

from core.mcp_server import create_server
from core.protocol.task import load_task


def make_root(tmp_path):
    root = tmp_path / "fleet"
    (root / "tasks").mkdir(parents=True)
    (root / "org.yaml").write_text(yaml.safe_dump({
        "org": "acme-inc",
        "departments": [{"id": "eng", "name": "Engineering"}],
        "agents": [{"id": "coder-1", "dept": "eng", "runtime": "codex", "node": "laptop"}],
        "nodes": [{"id": "laptop"}],
    }), encoding="utf-8")
    return root


def test_mcp_tools_complete_task_loop(tmp_path):
    async def scenario():
        root = make_root(tmp_path)
        server = create_server(root, "coder-1")
        async with create_connected_server_and_client_session(server) as session:
            names = {tool.name for tool in (await session.list_tools()).tools}
            assert names == {
                "task_list",
                "task_new",
                "task_update",
                "task_receipt",
                "my_tasks",
                "ready_work",
                "task_dependency_add",
                "task_dependency_remove",
            }
            update_schema = next(
                tool.inputSchema for tool in (await session.list_tools()).tools
                if tool.name == "task_update"
            )
            assert "doing" in str(update_schema["properties"]["status"])

            created = await session.call_tool("task_new", {
                "task_id": "task-20260720-101",
                "title": "Cold start proof",
                "priority": "high",
                "acceptance": ["task_list sees the card"],
            })
            assert not created.isError
            card = load_task(root / "tasks" / "task-20260720-101.yaml")
            assert card["priority"] == "high"
            assert card["acceptance"] == ["task_list sees the card"]
            ready = await session.call_tool("ready_work", {})
            assert "task-20260720-101" in ready.content[0].text
            claimed = await session.call_tool("task_update", {
                "task_id": "task-20260720-101", "status": "doing", "note": "claim"
            })
            assert not claimed.isError
            finished = await session.call_tool("task_update", {
                "task_id": "task-20260720-101", "status": "done", "note": "verified"
            })
            assert not finished.isError
            mine = await session.call_tool("my_tasks", {"include_terminal": True})
            assert not mine.isError
            receipt = await session.call_tool("task_receipt", {"task_id": "task-20260720-101"})
            assert not receipt.isError
            assert "doing → done" in receipt.content[0].text

    anyio.run(scenario)


def test_mcp_rejects_non_holder_update(tmp_path):
    """Security negative case: docs/security.md#sec-4-holder-only-writes."""
    async def scenario():
        root = make_root(tmp_path)
        owner = create_server(root, "coder-1")
        async with create_connected_server_and_client_session(owner) as session:
            await session.call_tool("task_new", {
                "task_id": "task-20260720-102", "title": "Protected card"
            })
        stranger = create_server(root, "other-agent")
        async with create_connected_server_and_client_session(stranger) as session:
            result = await session.call_tool("task_update", {
                "task_id": "task-20260720-102", "status": "doing", "note": "take"
            })
            assert result.isError
            assert "holder-only-writes" in result.content[0].text

    anyio.run(scenario)
