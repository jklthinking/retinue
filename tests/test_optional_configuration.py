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
