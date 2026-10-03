"""Central node telemetry uses an existing exact node scope and fixed source account."""
from __future__ import annotations

import datetime as dt
import json
import os
from pathlib import Path
import shlex
import sys
import types

import pytest

from tools import retinue_node_pull as pull
from test_server_api import client, login


NOW = dt.datetime(2026, 2, 3, 4, 5, tzinfo=dt.timezone.utc)


def config(tmp_path):
    return {"schema_version": 1, "node_id": "device-a", "label": "", "services": [],
            "url": "http://127.0.0.1:9219", "token_file": str(tmp_path / "existing-node.token"),
            "remote": {"host": "device-a", "probe_user": "observer", "probe_home": "/var/lib/example-probe-home",
                       "workspace": "/opt/neutral-observer", "python": "/usr/bin/python3"}}


def raw_snapshot():
    return {"schema_version": 1, "source": "node-runtime-probe", "privacy": "metadata",
            "observed_at": NOW.isoformat(),
            "heartbeat": {"id": "device-a", "label": "", "hostname": "synthetic-host", "platform": "synthetic-os",
                          "uptime_seconds": 120, "load": [0.1, 0.1, 0.1], "services": [],
                          "disk": {"total": 100, "used": 10, "free": 90, "percent": 10},
                          "memory": {"total": 500, "available": 400}},
            "runtimes": {"node_id": "device-a", "runtimes": [{"runtime": "claude-code", "command": "claude",
                           "available": True, "source": "well-known"}],
                         "data_dirs": [{"runtime": "claude-code", "path_hint": "~/.claude/projects", "last_changed_at": NOW.isoformat()}]}}


def observation():
    return pull.validate_snapshot(raw_snapshot(), "device-a", now=NOW)


def test_dry_run_reads_source_without_reading_token_or_calling_api(tmp_path):
    def forbidden(*args):
        raise AssertionError("dry run crossed credential or API boundary")

    result = pull.run_once(config(tmp_path), dry_run=True, source=lambda c: observation(), api=forbidden, token_reader=forbidden)
    assert result["status"] == "dry-run"
    assert result["runtime_ids"] == ["claude-code"]
    assert result["data_dirs_checked"] is True
    assert "hostname" not in result and "token_file" not in result


def test_metadata_whitelist_discards_extra_chat_and_config_fields():
    raw = raw_snapshot()
    raw["messages"] = [{"text": "PRIVATE BODY"}]
    raw["heartbeat"]["disk"]["secret"] = "PRIVATE DISK EXTRA"
    raw["runtimes"]["runtimes"][0]["provider_key"] = "PRIVATE PROVIDER EXTRA"
    raw["runtimes"]["data_dirs"][0]["absolute_path"] = "PRIVATE PATH EXTRA"
    result = pull.validate_snapshot(raw, "device-a", now=NOW)
    assert "PRIVATE" not in json.dumps(result)
    assert result["runtimes"]["runtimes"][0]["available"] is True


def test_older_inventory_preserves_unknown_data_directory_coverage():
    raw = raw_snapshot()
    raw["runtimes"].pop("data_dirs")
    result = pull.validate_snapshot(raw, "device-a", now=NOW)
    assert "data_dirs" not in result["runtimes"]
    raw["runtimes"]["data_dirs"] = []
    assert pull.validate_snapshot(raw, "device-a", now=NOW)["runtimes"]["data_dirs"] == []


@pytest.mark.parametrize("problem", ["wrong-node", "old", "future", "naive", "absolute-hint", "invalid-count"])
def test_unverified_source_never_becomes_fresh_available_inventory(problem):
    raw = raw_snapshot()
    if problem == "wrong-node":
        raw["runtimes"]["node_id"] = "another-device"
    elif problem == "old":
        raw["observed_at"] = (NOW - dt.timedelta(minutes=3)).isoformat()
    elif problem == "future":
        raw["observed_at"] = (NOW + dt.timedelta(minutes=3)).isoformat()
    elif problem == "naive":
        raw["observed_at"] = "2026-02-03T04:05:00"
    elif problem == "absolute-hint":
        raw["runtimes"]["data_dirs"][0]["path_hint"] = "/private/runtime-data"
    else:
        raw["heartbeat"]["uptime_seconds"] = True
    with pytest.raises(pull.NodePullError):
        pull.validate_snapshot(raw, "device-a", now=NOW)


def test_operator_paths_and_labels_are_data_in_a_fixed_ssh_argv(tmp_path):
    settings = config(tmp_path)
    settings["label"] = "A label with ' punctuation; echo data"
    settings["remote"]["workspace"] = "/opt/neutral source with spaces"
    argv = pull.ssh_argv(settings)
    remote = shlex.split(argv[-1])
    assert remote[:6] == ["/usr/sbin/runuser", "-u", "observer", "--", "/usr/bin/env", "-i"]
    assert "HOME=/var/lib/example-probe-home" in remote
    assert "PYTHONPATH=/opt/neutral source with spaces" in remote
    assert remote[-2] == pull.REMOTE_PROGRAM
    assert json.loads(remote[-1])["label"] == settings["label"]
    assert "StrictHostKeyChecking=yes" in argv
    assert settings["token_file"] not in " ".join(argv)
    settings["remote"]["host"] = "-oProxyCommand=data"
    with pytest.raises(pull.NodePullError):
        pull.ssh_argv(settings)


@pytest.mark.parametrize("url", ["https://example.invalid", "http://127.0.0.1@external.invalid", "http://127.0.0.1:9219/api?data=private"])
def test_report_base_cannot_send_existing_node_credential_off_host(tmp_path, url):
    settings = config(tmp_path)
    settings["url"] = url
    path = tmp_path / "config.json"
    path.write_text(json.dumps(settings))
    with pytest.raises(pull.NodePullError, match="loopback"):
        pull.load_config(path)


def test_http_redirect_is_refused_without_forwarding_authorization():
    with pytest.raises(pull.NodePullError, match="redirect-refused"):
        pull.NoRedirect().redirect_request(None, None, 302, "redirect", {}, "https://example.invalid")


@pytest.mark.parametrize("code", [401, 403, 404])
def test_failed_heartbeat_authorization_stops_before_runtime_report(tmp_path, code):
    calls = []
    def api(url, token, path, payload):
        calls.append(path)
        raise pull.NodePullError("node-api-http-" + str(code))
    with pytest.raises(pull.NodePullError, match="http-" + str(code)):
        pull.run_once(config(tmp_path), dry_run=False, source=lambda c: observation(), api=api, token_reader=lambda p: "test-only-node-token")
    assert calls == ["/api/nodes/heartbeat"]


def test_existing_node_token_api_roundtrip_is_repeatable_without_growing_roster(client, tmp_path):
    from server.db import Node, NodeRuntime, Task
    login(client)
    issued = client.post("/api/admin/node-tokens", json={"node_id": "device-a"})
    assert issued.status_code == 200
    token = issued.json()["token"]
    client.cookies.clear()
    calls = []
    def api(url, credential, endpoint, payload):
        calls.append(endpoint)
        response = client.post(endpoint, headers={"Authorization": "Bearer " + credential}, json=payload)
        if response.status_code != 200:
            raise pull.NodePullError("node-api-http-" + str(response.status_code))
        return response.json()
    for _ in range(2):
        result = pull.run_once(config(tmp_path), dry_run=False, source=lambda c: observation(), api=api, token_reader=lambda p: token)
        assert result["status"] == "reported"
    assert calls == ["/api/nodes/heartbeat", "/api/nodes/runtimes"] * 2
    with client.app.state.session_factory() as db:
        assert db.query(Node).count() == db.query(NodeRuntime).count() == 1
        assert db.query(Task).count() == 0
        assert db.query(NodeRuntime).one().path_hint == "~/.claude/projects"


def test_a_different_nodes_token_cannot_create_or_refresh_the_target(client, tmp_path):
    from server.db import Node, NodeRuntime
    login(client)
    issued = client.post("/api/admin/node-tokens", json={"node_id": "another-device"})
    token = issued.json()["token"]
    client.cookies.clear()
    calls = []
    def api(url, credential, endpoint, payload):
        calls.append(endpoint)
        response = client.post(endpoint, headers={"Authorization": "Bearer " + credential}, json=payload)
        raise pull.NodePullError("node-api-http-" + str(response.status_code))
    with pytest.raises(pull.NodePullError, match="http-403"):
        pull.run_once(config(tmp_path), dry_run=False, source=lambda c: observation(), api=api, token_reader=lambda p: token)
    assert calls == ["/api/nodes/heartbeat"]
    with client.app.state.session_factory() as db:
        assert db.get(Node, "device-a") is None
        assert db.query(NodeRuntime).count() == 0


def test_fixed_remote_program_checks_account_and_home_before_collecting(monkeypatch, tmp_path, capsys):
    from node import probe, runtime_probe
    calls = []
    monkeypatch.setattr(probe, "collect", lambda *args: calls.append("heartbeat") or raw_snapshot()["heartbeat"])
    monkeypatch.setattr(runtime_probe, "collect", lambda *args: calls.append("runtimes") or raw_snapshot()["runtimes"])
    monkeypatch.setattr(os, "geteuid", lambda: 1000, raising=False)
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    account = types.SimpleNamespace(pw_name="observer", pw_dir=str(tmp_path))
    monkeypatch.setitem(sys.modules, "pwd", types.SimpleNamespace(getpwuid=lambda uid: account))
    params = {"node_id": "device-a", "label": "", "services": [], "probe_user": "another-account"}
    monkeypatch.setattr(sys, "argv", ["fixed-program", json.dumps(params)])
    with pytest.raises(SystemExit) as error:
        exec(pull.REMOTE_PROGRAM, {})
    assert error.value.code == 1 and calls == []
    assert "fixed-node-probe-failed" in capsys.readouterr().out
    params["probe_user"] = "observer"
    monkeypatch.setattr(sys, "argv", ["fixed-program", json.dumps(params)])
    exec(pull.REMOTE_PROGRAM, {})
    result = json.loads(capsys.readouterr().out)
    assert calls == ["heartbeat", "runtimes"]
    assert result["privacy"] == "metadata"
    monkeypatch.setattr(os, "geteuid", lambda: 0)
    with pytest.raises(SystemExit):
        exec(pull.REMOTE_PROGRAM, {})
    assert calls == ["heartbeat", "runtimes"]


def test_private_existing_token_is_read_without_export(tmp_path):
    path = tmp_path / "existing-node.token"
    path.write_text("test-only-node-token\n")
    path.chmod(0o600)
    assert pull.read_token(str(path)) == "test-only-node-token"


@pytest.mark.skipif(os.name == "nt", reason="POSIX file privacy mode")
def test_unix_token_file_must_remain_private_and_not_a_symlink(tmp_path):
    path = tmp_path / "existing-node.token"
    path.write_text("test-only-node-token\n")
    path.chmod(0o644)
    with pytest.raises(pull.NodePullError, match="not-private"):
        pull.read_token(str(path))
    path.chmod(0o600)
    link = tmp_path / "linked-node.token"
    link.symlink_to(path)
    with pytest.raises(pull.NodePullError, match="unreadable"):
        pull.read_token(str(link))
