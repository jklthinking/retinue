"""Node agent entry point: server-stack independence and payload parity.

The first test is the core claim of the node work order: the node duties
import and run while the server package's third-party dependencies (web
framework, ORM, ASGI server, crypto) cannot be imported at all.  The rest
prove the ``retinue-node`` subcommands produce byte-identical payloads to the
existing ``server.main`` subcommands.
"""

from __future__ import annotations

import collections
import importlib
import json
import sqlite3
import sys

import pytest

# Third-party packages pulled in by the server extra only (pyproject.toml).
SERVER_STACK = ("fastapi", "starlette", "uvicorn", "sqlalchemy", "cryptography")

URL = "http://127.0.0.1:9219"


class _Blocker:
    """Meta-path finder that makes the server stack unimportable."""

    def find_spec(self, name, path=None, target=None):
        if name.partition(".")[0] in SERVER_STACK:
            raise ModuleNotFoundError(f"blocked by test: {name}")
        return None


def _module_names(prefixes):
    return {
        key
        for key in sys.modules
        if any(key == prefix or key.startswith(prefix + ".") for prefix in prefixes)
    }


def test_node_duties_run_without_server_stack(capsys):
    pre_existing_server = _module_names(("server",))
    # Force a genuinely fresh import of the node package under the blocker,
    # and make sure no cached server-stack module can be reused.
    saved = {}
    for key in list(sys.modules):
        root = key.partition(".")[0]
        if root == "node" or root in SERVER_STACK:
            saved[key] = sys.modules.pop(key)
    blocker = _Blocker()
    sys.meta_path.insert(0, blocker)
    try:
        cli = importlib.import_module("node.cli")
        exit_code = cli.main(["whoami", "--node", "synthetic-node"])
        assert exit_code == 0
        report = json.loads(capsys.readouterr().out)
        assert report["node_id"] == "synthetic-node"
        # No server module was imported as a side effect, and no
        # server-stack dependency entered the process.
        assert _module_names(("server",)) == pre_existing_server
        assert _module_names(SERVER_STACK) == set()
    finally:
        sys.meta_path.remove(blocker)
        # Drop the modules imported under the blocker and restore the
        # pre-test import state so shim identity holds for other tests.
        for key in list(sys.modules):
            if key.partition(".")[0] == "node":
                sys.modules.pop(key)
        sys.modules.update(saved)


def test_importing_and_using_node_touches_no_database(tmp_path, monkeypatch, capsys):
    def forbidden_database_open(*args, **kwargs):
        raise AssertionError("node duties must not open a database")

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(sqlite3, "connect", forbidden_database_open)
    import node.cli

    assert node.cli.main(["whoami", "--node", "synthetic-node"]) == 0
    capsys.readouterr()
    assert not list(tmp_path.rglob("*.db"))


@pytest.fixture()
def token_file(tmp_path):
    path = tmp_path / "node-token"
    path.write_text("synthetic-token\n", encoding="utf-8")
    return path


@pytest.fixture()
def stable_probe(monkeypatch):
    """Pin every volatile input of the heartbeat collector."""
    import node.probe as probe

    monkeypatch.setattr(probe.socket, "gethostname", lambda: "synthetic-host")
    monkeypatch.setattr(probe.platform_mod, "platform", lambda: "SyntheticOS 1.0")
    monkeypatch.setattr(probe, "_uptime_seconds", lambda: 3600)
    monkeypatch.setattr(
        probe,
        "_memory",
        lambda: {"total": 8, "available": 4, "swap_total": 2, "swap_free": 1},
    )
    monkeypatch.setattr(probe.os, "getloadavg", lambda: (0.5, 0.25, 0.125))
    usage = collections.namedtuple("usage", ["total", "used", "free"])
    monkeypatch.setattr(probe.shutil, "disk_usage", lambda root: usage(100, 40, 60))
    return probe


def test_server_shims_alias_node_modules():
    import node.http_client
    import node.probe
    import node.push_sessions
    import node.runtime_probe
    import server.http_client
    import server.probe
    import server.push_sessions
    import server.runtime_probe

    assert server.http_client is node.http_client
    assert server.probe is node.probe
    assert server.push_sessions is node.push_sessions
    assert server.runtime_probe is node.runtime_probe


def test_heartbeat_payload_matches_server_main(monkeypatch, token_file, stable_probe, capsys):
    import node.cli
    import server.main

    sent = []
    monkeypatch.setattr(
        stable_probe, "push", lambda url, token, payload: sent.append((url, token, payload))
    )

    assert server.main.main(
        ["probe", "--node", "node-a", "--label", "lab", "--url", URL,
         "--token-file", str(token_file)]
    ) == 0
    assert node.cli.main(
        ["heartbeat", "--node", "node-a", "--label", "lab", "--url", URL,
         "--token-file", str(token_file)]
    ) == 0

    assert len(sent) == 2
    assert sent[0] == sent[1]
    url, token, payload = sent[1]
    assert url == URL
    assert token == "synthetic-token"
    assert payload["id"] == "node-a"
    assert payload["hostname"] == "synthetic-host"


def test_runtimes_payload_matches_server_main(monkeypatch, token_file, capsys):
    import node.cli
    import node.runtime_probe as runtime_probe
    import server.main

    paths = {"codex": "/opt/bin/codex", "claude": "/opt/bin/claude"}

    def fake_which(command, path=None):
        return paths.get(command) if path is None else None

    monkeypatch.setattr(runtime_probe.shutil, "which", fake_which)
    sent = []
    monkeypatch.setattr(
        runtime_probe, "push", lambda url, token, payload: sent.append((url, token, payload))
    )

    assert server.main.main(
        ["probe-runtimes", "--node", "node-a", "--url", URL,
         "--token-file", str(token_file)]
    ) == 0
    assert node.cli.main(
        ["runtimes", "--node", "node-a", "--url", URL,
         "--token-file", str(token_file)]
    ) == 0

    assert len(sent) == 2
    assert sent[0] == sent[1]
    payload = sent[1][2]
    assert payload["node_id"] == "node-a"
    # Privacy contract: runtime id, executable basename, availability — never
    # an absolute path.
    for entry in payload["runtimes"]:
        assert set(entry) == {"runtime", "command", "available", "source"}
        assert not entry["command"].startswith("/")
    # Data-directory hints follow the same contract: tilde-relative only.
    for entry in payload["data_dirs"]:
        assert set(entry) == {"runtime", "path_hint", "last_changed_at"}
        assert entry["path_hint"].startswith("~/")


def test_sync_sessions_payload_matches_server_main(monkeypatch, token_file, tmp_path, capsys):
    import node.cli
    import node.push_sessions as push_sessions
    import server.main

    source = tmp_path / "sessions"
    source.mkdir()
    rows = [
        {
            "external_id": "synthetic-session",
            "actor_id": "agent-1",
            "runtime": "codex",
            "privacy": "metadata",
        }
    ]
    monkeypatch.setattr(push_sessions, "collect_sessions", lambda *a, **kw: rows)
    sent = []

    def fake_push(url, token, pushed_rows):
        sent.append((url, token, pushed_rows))
        return {"created": len(pushed_rows), "updated": 0, "unchanged": 0}

    monkeypatch.setattr(push_sessions, "push", fake_push)

    common = [
        "--runtime", "codex", "--source", str(source), "--actor", "agent-1",
        "--url", URL, "--token-file", str(token_file), "--privacy", "metadata",
    ]
    assert server.main.main(["sync-sessions", *common]) == 0
    assert node.cli.main(["sync-sessions", *common]) == 0

    assert len(sent) == 2
    assert sent[0] == sent[1]
    assert sent[1] == (URL, "synthetic-token", rows)


def test_whoami_sends_nothing(monkeypatch, token_file, stable_probe, capsys):
    import node.cli
    import node.runtime_probe as runtime_probe

    def forbidden(*args, **kwargs):
        raise AssertionError("whoami must not send anything")

    monkeypatch.setattr(stable_probe, "push", forbidden)
    monkeypatch.setattr(runtime_probe, "push", forbidden)

    assert node.cli.main(
        ["whoami", "--node", "node-a", "--token-file", str(token_file)]
    ) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["node_id"] == "node-a"
    assert report["server_url"] == URL
    assert report["token_file"] == {"path": str(token_file), "readable": True}
    assert report["heartbeat"]["id"] == "node-a"
    assert report["runtimes"]["node_id"] == "node-a"
    assert "sessions" not in report


def test_whoami_sessions_preview(monkeypatch, token_file, stable_probe, tmp_path, capsys):
    import node.cli

    source = tmp_path / "sessions"
    source.mkdir()
    assert node.cli.main(
        ["whoami", "--node", "node-a", "--token-file", str(token_file),
         "--runtime", "codex", "--source", str(source), "--actor", "agent-1"]
    ) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["sessions"] == []


def test_config_from_environment(monkeypatch, token_file, capsys):
    import node.cli
    import node.runtime_probe as runtime_probe

    monkeypatch.setattr(runtime_probe.shutil, "which", lambda command, path=None: None)
    monkeypatch.setattr("node.data_dirs.scan", lambda home=None: [])
    monkeypatch.setenv("RETINUE_SERVER_URL", URL)
    monkeypatch.setenv("RETINUE_NODE_ID", "env-node")
    monkeypatch.setenv("RETINUE_NODE_TOKEN_FILE", str(token_file))
    sent = []
    monkeypatch.setattr(
        runtime_probe, "push", lambda url, token, payload: sent.append((url, token, payload))
    )

    assert node.cli.main(["runtimes"]) == 0
    assert sent == [
        (URL, "synthetic-token", {"node_id": "env-node", "runtimes": [], "data_dirs": []})
    ]


def test_node_id_is_required(monkeypatch, capsys):
    import node.cli

    monkeypatch.delenv("RETINUE_NODE_ID", raising=False)
    with pytest.raises(SystemExit):
        node.cli.main(["whoami"])
