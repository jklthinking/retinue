"""Packaging guarantees: a base or node install stays minimal, and the MCP
surfaces refuse helpfully when their extra is absent.

The dependency assertions run against the declared metadata in
pyproject.toml — never against whatever happens to be installed in the
current environment, which would make the test agree with any broken
environment it ran in.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from _manifest import load_manifest

ROOT = Path(__file__).resolve().parents[1]

# An ASGI server, a crypto library, or an HTTP client on a managed node is
# dead weight the operator never asked for; the mcp package pulls in all
# three, which is why it is an extra now.
ASGI_SERVERS = {"uvicorn", "hypercorn", "daphne", "granian", "starlette"}
CRYPTO_LIBRARIES = {"cryptography", "pynacl", "pyopenssl", "pyjwt", "jwt"}
HTTP_CLIENTS = {"httpx", "requests", "aiohttp"}


def _manifest() -> dict:
    return load_manifest()


def _requirement_name(requirement: str) -> str:
    """The distribution name of one requirement, without extras, version
    bounds, or environment markers."""
    return re.split(r"[<>=!~;\s\[]", requirement, maxsplit=1)[0].lower()


def _resolve_extra(extras: dict[str, list[str]], name: str) -> set[str]:
    """The flattened distribution names an extra pulls in, following the
    manifest's self-references (``retinue[server]`` and friends)."""
    resolved: set[str] = set()
    for requirement in extras[name]:
        if _requirement_name(requirement) == "retinue":
            inner = re.search(r"\[([^\]]+)\]", requirement)
            assert inner, f"self-reference without extras: {requirement}"
            for sub in inner.group(1).split(","):
                resolved |= _resolve_extra(extras, sub.strip())
        else:
            resolved.add(_requirement_name(requirement))
    return resolved


def _base_dependencies() -> set[str]:
    return {
        _requirement_name(requirement)
        for requirement in _manifest()["project"]["dependencies"]
    }


def test_base_install_has_no_asgi_server_and_no_crypto_library():
    base = _base_dependencies()
    heavy = ASGI_SERVERS | CRYPTO_LIBRARIES | HTTP_CLIENTS | {"mcp"}
    assert base.isdisjoint(heavy), f"base dependencies drag in: {base & heavy}"


def test_node_extra_adds_nothing_heavy_either():
    manifest = _manifest()
    node = _base_dependencies() | _resolve_extra(
        manifest["project"]["optional-dependencies"], "node"
    )
    heavy = ASGI_SERVERS | CRYPTO_LIBRARIES | HTTP_CLIENTS | {"mcp"}
    assert node.isdisjoint(heavy), f"node install drags in: {node & heavy}"


def test_mcp_is_an_extra_and_the_documented_server_install_keeps_it():
    extras = _manifest()["project"]["optional-dependencies"]
    assert "mcp" in _resolve_extra(extras, "mcp")
    # The MCP bridge is a server-side surface (server mcp); an install that
    # predates the split documented '.[server]', so that path must not lose
    # the bridge.
    assert "mcp" in _resolve_extra(extras, "server")
    assert "mcp" in _resolve_extra(extras, "test")


def test_mcp_server_refuses_naming_the_extra_when_absent(monkeypatch, tmp_path):
    import core.mcp_server

    monkeypatch.setattr(core.mcp_server, "FastMCP", None)

    with pytest.raises(SystemExit) as excinfo:
        core.mcp_server.create_server(tmp_path, "agent-1")

    message = str(excinfo.value)
    assert "retinue[mcp]" in message
    assert not isinstance(excinfo.value, ImportError)


def test_mcp_bridge_refuses_naming_the_extra_when_absent(monkeypatch):
    import server.mcp_bridge

    monkeypatch.setattr(server.mcp_bridge, "FastMCP", None)

    with pytest.raises(SystemExit) as excinfo:
        server.mcp_bridge.main()

    message = str(excinfo.value)
    assert "retinue[mcp]" in message
    assert not isinstance(excinfo.value, ImportError)


def test_mcp_modules_import_without_touching_the_network_or_filesystem():
    """Both MCP modules must import cleanly whether or not the extra is
    present: the refusal happens when the surface is used, not at import."""
    import core.mcp_server
    import server.mcp_bridge

    assert callable(core.mcp_server.create_server)
    assert callable(server.mcp_bridge.create_server)
