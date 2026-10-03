"""Optional integrations stay inert when deployment configuration is absent."""

from __future__ import annotations

import asyncio
import importlib
import json
import sys

import pytest


def _reload(name: str):
    module = importlib.import_module(name)
    return importlib.reload(module)


def test_kingdom_imports_without_machine_specific_defaults(monkeypatch):
    for name in (
        "RETINUE_KINGDOM_ROOT",
        "RETINUE_KINGDOM_CASTLE_ROOT",
        "RETINUE_KINGDOM_CASTLE_HOST",
        "RETINUE_KINGDOM_VAULT",
    ):
        monkeypatch.delenv(name, raising=False)

    kingdom = _reload("server.kingdom")

    assert kingdom.HERMES_ROOT is None
    assert kingdom.CASTLE_ROOT is None
    assert kingdom.DATA_DIR is None
    assert kingdom.ACTION_SCRIPT is None
    assert kingdom.CASTLE_ACTION_SCRIPT is None
    assert kingdom.OBSERVER_SCRIPT is None
    assert kingdom.CASTLE_HOST is None
    assert kingdom.VAULT_CANDIDATES == ()
    assert kingdom._read_snapshot("node-a") is None
    assert kingdom._read_audit("node-a") == {
        "ok": False,
        "items": [],
        "reason": "kingdom root is not configured",
    }
    assert kingdom._read_audit("node-b") == {
        "ok": False,
        "items": [],
        "reason": "kingdom root is not configured",
    }
    assert kingdom._list_conflicts() == {"available": False, "items": []}

    result = kingdom._run_action({"node": "node-a", "action": "snapshot_refresh"})
    assert result == {
        "ok": False,
        "error": "RETINUE_KINGDOM_ROOT is not configured",
    }
    overview = kingdom._combined_overview()
    assert overview["nodes"] == []
    assert overview["alerts"]
    assert all("not configured" in row["detail"] for row in overview["alerts"])


def test_node_b_root_drives_remote_commands_and_falls_back_to_local(monkeypatch, tmp_path):
    kingdom_root = tmp_path / "local-kingdom"
    node_b_root = tmp_path / "remote-kingdom"
    vault_root = tmp_path / "vault"
    monkeypatch.setenv("RETINUE_KINGDOM_ROOT", str(kingdom_root))
    monkeypatch.setenv("RETINUE_KINGDOM_CASTLE_ROOT", str(node_b_root))
    monkeypatch.setenv("RETINUE_KINGDOM_CASTLE_HOST", "node-b-node")
    monkeypatch.setenv("RETINUE_KINGDOM_VAULT", str(vault_root))
    kingdom = _reload("server.kingdom")
    calls = []

    def fake_run(argv, **kwargs):
        calls.append((argv, kwargs))
        return type("Result", (), {"returncode": 0, "stdout": "", "stderr": ""})()

    monkeypatch.setattr(kingdom.subprocess, "run", fake_run)
    assert kingdom._read_audit("node-b") == {"ok": True, "items": []}
    assert kingdom._run_action({"node": "node-b", "action": "snapshot_refresh"}) == {
        "ok": True,
        "node": "node-b",
    }
    assert calls[0][0][-1] == str(node_b_root / "logs" / "kingdom-actions.jsonl")
    assert calls[1][0][-2] == str(node_b_root / "scripts" / "kingdom_action.py")
    assert kingdom.VAULT_CANDIDATES == (vault_root,)

    monkeypatch.delenv("RETINUE_KINGDOM_CASTLE_ROOT")
    kingdom = _reload("server.kingdom")
    calls.clear()
    monkeypatch.setattr(kingdom.subprocess, "run", fake_run)

    assert kingdom.CASTLE_ROOT == kingdom.HERMES_ROOT == kingdom_root
    assert kingdom._read_audit("node-b") == {"ok": True, "items": []}
    assert kingdom._run_action({"node": "node-b", "action": "snapshot_refresh"}) == {
        "ok": True,
        "node": "node-b",
    }
    assert calls[0][0][-1] == str(kingdom_root / "logs" / "kingdom-actions.jsonl")
    assert calls[1][0][-2] == str(kingdom_root / "scripts" / "kingdom_action.py")


def test_failed_remote_audit_is_a_redacted_gap_not_an_empty_log(monkeypatch, tmp_path):
    kingdom_root = tmp_path / "local-kingdom"
    node_b_root = tmp_path / "remote-kingdom"
    node_b_host = "node-b-node"
    monkeypatch.setenv("RETINUE_KINGDOM_ROOT", str(kingdom_root))
    monkeypatch.setenv("RETINUE_KINGDOM_CASTLE_ROOT", str(node_b_root))
    monkeypatch.setenv("RETINUE_KINGDOM_CASTLE_HOST", node_b_host)
    kingdom = _reload("server.kingdom")

    def failed_run(argv, **kwargs):
        del argv, kwargs
        diagnostic = f"could not reach {node_b_host}; remote path {node_b_root}"
        return type("Result", (), {"returncode": 255, "stdout": "", "stderr": diagnostic})()

    async def inline_to_thread(function, *args):
        return function(*args)

    monkeypatch.setattr(kingdom.subprocess, "run", failed_run)
    monkeypatch.setattr(kingdom.asyncio, "to_thread", inline_to_thread)

    response = asyncio.run(kingdom.kingdom_audit())
    assert response == {
        "items": [],
        "gaps": [{
            "node": "node-b",
            "reason": "remote command failed",
            "returncode": 255,
        }],
    }
    action = kingdom._run_action({"node": "node-b", "action": "snapshot_refresh"})
    assert action == {
        "ok": False,
        "error": "remote action failed",
        "returncode": 255,
        "node": "node-b",
    }
    caller_text = json.dumps({"audit": response, "action": action})
    assert node_b_host not in caller_text
    assert str(kingdom_root) not in caller_text
    assert str(node_b_root) not in caller_text
    assert "/" not in caller_text


def test_genuinely_empty_remote_audit_keeps_the_existing_response(monkeypatch, tmp_path):
    monkeypatch.setenv("RETINUE_KINGDOM_ROOT", str(tmp_path / "local-kingdom"))
    monkeypatch.setenv("RETINUE_KINGDOM_CASTLE_ROOT", str(tmp_path / "remote-kingdom"))
    monkeypatch.setenv("RETINUE_KINGDOM_CASTLE_HOST", "node-b-node")
    kingdom = _reload("server.kingdom")

    def successful_run(argv, **kwargs):
        del argv, kwargs
        return type("Result", (), {"returncode": 0, "stdout": "", "stderr": ""})()

    async def inline_to_thread(function, *args):
        return function(*args)

    monkeypatch.setattr(kingdom.subprocess, "run", successful_run)
    monkeypatch.setattr(kingdom.asyncio, "to_thread", inline_to_thread)

    assert asyncio.run(kingdom.kingdom_audit()) == {"items": []}


def test_node_b_action_reports_missing_host_configuration(monkeypatch, tmp_path):
    monkeypatch.setenv("RETINUE_KINGDOM_ROOT", str(tmp_path / "kingdom"))
    monkeypatch.delenv("RETINUE_KINGDOM_CASTLE_ROOT", raising=False)
    monkeypatch.delenv("RETINUE_KINGDOM_CASTLE_HOST", raising=False)
    monkeypatch.delenv("RETINUE_KINGDOM_VAULT", raising=False)
    kingdom = _reload("server.kingdom")

    result = kingdom._run_action({"node": "node-b", "action": "snapshot_refresh"})

    assert result == {
        "ok": False,
        "error": "RETINUE_KINGDOM_CASTLE_HOST is not configured",
    }


def test_mcp_bridge_imports_without_connection_configuration(monkeypatch):
    monkeypatch.delenv("RETINUE_SERVER_URL", raising=False)
    monkeypatch.delenv("RETINUE_TOKEN", raising=False)
    # A token file left in the developer's environment would otherwise supply a
    # credential and make "no configuration" untrue.
    monkeypatch.delenv("RETINUE_TOKEN_FILE", raising=False)
    bridge = _reload("server.mcp_bridge")

    with pytest.raises(bridge.BridgeError, match="RETINUE_SERVER_URL"):
        bridge._call("GET", "/api/auth/me")


def test_mcp_bridge_reads_its_token_from_a_file(tmp_path, monkeypatch):
    """A configuration file may name a path instead of carrying the token itself.

    An agent runtime's MCP configuration often lives inside a project directory, where a
    raw token can be committed. This is the same reason the node duties take
    RETINUE_NODE_TOKEN_FILE.
    """
    token_file = tmp_path / "agent.token"
    token_file.write_text("rtn_from_file\n", encoding="utf-8")
    token_file.chmod(0o600)
    monkeypatch.setenv("RETINUE_TOKEN_FILE", str(token_file))
    monkeypatch.delenv("RETINUE_TOKEN", raising=False)
    bridge = _reload("server.mcp_bridge")

    # Trailing newline stripped: an operator writing the file with an editor or a
    # shell redirect should not have to think about it.
    assert bridge._token() == "rtn_from_file"


def test_mcp_bridge_rejects_a_group_or_world_readable_token_file(
    tmp_path, monkeypatch
):
    token_file = tmp_path / "agent.token"
    token_file.write_text("rtn_exposed\n", encoding="utf-8")
    token_file.chmod(0o644)
    monkeypatch.setenv("RETINUE_TOKEN_FILE", str(token_file))
    bridge = _reload("server.mcp_bridge")

    with pytest.raises(bridge.BridgeError) as exc_info:
        bridge._token()

    message = str(exc_info.value)
    assert str(token_file) in message
    assert "644" in message
    assert "chmod 600" in message


def test_mcp_bridge_does_not_apply_unix_modes_on_other_platforms(
    tmp_path, monkeypatch
):
    token_file = tmp_path / "agent.token"
    token_file.write_text("rtn_non_unix\n", encoding="utf-8")
    token_file.chmod(0o644)
    monkeypatch.setenv("RETINUE_TOKEN_FILE", str(token_file))
    bridge = _reload("server.mcp_bridge")
    monkeypatch.setattr(bridge, "_uses_unix_file_permissions", lambda: False)

    assert bridge._token() == "rtn_non_unix"


def test_mcp_bridge_still_accepts_the_token_in_the_environment(monkeypatch):
    monkeypatch.delenv("RETINUE_TOKEN_FILE", raising=False)
    monkeypatch.setenv("RETINUE_TOKEN", "rtn_from_env")
    bridge = _reload("server.mcp_bridge")

    assert bridge._token() == "rtn_from_env"


def test_mcp_bridge_names_the_cause_when_the_token_file_is_unusable(
    tmp_path, monkeypatch
):
    """Fail loudly rather than falling back to the variable.

    Silently using RETINUE_TOKEN when the named file is missing would let a typo in the
    path go unnoticed, and the operator who wrote the path is entitled to be told it did
    not work.
    """
    bridge = _reload("server.mcp_bridge")

    monkeypatch.setenv("RETINUE_TOKEN", "rtn_should_not_be_used")
    monkeypatch.setenv("RETINUE_TOKEN_FILE", str(tmp_path / "absent.token"))
    with pytest.raises(bridge.BridgeError, match="cannot read RETINUE_TOKEN_FILE"):
        bridge._token()

    empty = tmp_path / "empty.token"
    empty.write_text("", encoding="utf-8")
    empty.chmod(0o600)
    monkeypatch.setenv("RETINUE_TOKEN_FILE", str(empty))
    with pytest.raises(bridge.BridgeError, match="is empty"):
        bridge._token()


def test_vault_recap_queue_requires_explicit_database(monkeypatch):
    queue = _reload("scripts.vault_recap_queue")
    monkeypatch.setattr(sys, "argv", ["vault_recap_queue.py", "list"])

    with pytest.raises(SystemExit) as exc_info:
        queue.main()

    assert exc_info.value.code == 2
