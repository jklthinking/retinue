"""Fleet-view inventory truth: probed vs never probed, and data-dir signals.

A node that reports an empty inventory has been *probed*; a node that has
never reported has not. The API and the panel must tell "asked and found
none" apart from "never asked", and a probe that predates data-directory
reporting must read as "history unknown", never as "no local history".
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import inspect, select

from server.app import create_app
from server.db import (
    LATEST_SCHEMA_VERSION,
    Actor,
    SchemaVersion,
    User,
    make_session_factory,
    migrate_database,
)
from server.security import hash_password


@pytest.fixture()
def client(tmp_path):
    factory = make_session_factory(tmp_path / "test.db")
    with factory() as db:
        db.add(Actor(id="owner", kind="human", display_name="负责人"))
        db.add(
            User(
                username="owner",
                password_hash=hash_password("owner-pass-123"),
                role="admin",
                actor_id="owner",
            )
        )
        db.commit()
    return TestClient(create_app(factory, data_dir=tmp_path))


def _login(client: TestClient) -> None:
    response = client.post(
        "/api/auth/login", json={"username": "owner", "password": "owner-pass-123"}
    )
    assert response.status_code == 200


def _node_headers(client: TestClient, node_id: str) -> dict[str, str]:
    issued = client.post("/api/admin/node-tokens", json={"node_id": node_id})
    assert issued.status_code == 200
    return {"Authorization": f"Bearer {issued.json()['token']}"}


def _node(client: TestClient, node_id: str) -> dict:
    nodes = client.get("/api/nodes").json()
    return next(node for node in nodes if node["id"] == node_id)


def test_three_states_are_distinguishable_through_the_api(client):
    _login(client)
    headers = _node_headers(client, "workstation")
    beat = {"id": "workstation", "hostname": "synthetic-host"}
    assert client.post("/api/nodes/heartbeat", json=beat, headers=headers).status_code == 200

    # State 1: heartbeats arrive, but no runtime probe has ever run.
    node = _node(client, "workstation")
    assert node["runtime_state"] == "never_probed"
    assert node["runtimes_probed_at"] is None

    # State 2: the probe ran and truthfully found nothing.
    empty = client.post(
        "/api/nodes/runtimes", json={"node_id": "workstation", "runtimes": []},
        headers=headers,
    )
    assert empty.status_code == 200
    node = _node(client, "workstation")
    assert node["runtime_state"] == "probed_empty"
    assert node["runtimes_probed_at"] is not None

    # State 3: the probe found a runtime.
    found = client.post(
        "/api/nodes/runtimes",
        json={
            "node_id": "workstation",
            "runtimes": [{"runtime": "codex", "command": "codex"}],
        },
        headers=headers,
    )
    assert found.status_code == 200
    node = _node(client, "workstation")
    assert node["runtime_state"] == "probed_found"
    assert node["runtimes"][0]["runtime"] == "codex"


def test_empty_inventory_is_recorded_as_probed_not_absent(client):
    _login(client)
    quiet_headers = _node_headers(client, "quiet-node")
    fresh_headers = _node_headers(client, "fresh-node")
    for node_id, headers in (("quiet-node", quiet_headers), ("fresh-node", fresh_headers)):
        assert client.post(
            "/api/nodes/heartbeat", json={"id": node_id}, headers=headers
        ).status_code == 200

    # quiet-node reports an empty inventory; fresh-node never reports at all.
    response = client.post(
        "/api/nodes/runtimes", json={"node_id": "quiet-node", "runtimes": []},
        headers=quiet_headers,
    )
    assert response.status_code == 200
    assert response.json()["runtimes_probed_at"] is not None

    quiet = _node(client, "quiet-node")
    fresh = _node(client, "fresh-node")
    assert quiet["runtime_state"] == "probed_empty"
    assert quiet["runtimes_probed_at"] is not None
    assert fresh["runtime_state"] == "never_probed"
    assert fresh["runtimes_probed_at"] is None


def test_legacy_probe_payload_accepted_and_history_stays_unknown(client):
    _login(client)
    headers = _node_headers(client, "workstation")

    # An older probe reports executables only: no data_dirs key at all.
    legacy = client.post(
        "/api/nodes/runtimes",
        json={
            "node_id": "workstation",
            "runtimes": [{"runtime": "codex", "command": "codex"}],
        },
        headers=headers,
    )
    assert legacy.status_code == 200

    node = _node(client, "workstation")
    assert node["data_dirs_probed_at"] is None
    entry = node["runtimes"][0]
    assert entry["data_state"] == "unknown"  # never "none": the probe cannot tell
    assert entry["path_hint"] is None
    stored = client.get("/api/nodes/workstation/runtimes").json()
    assert stored[0]["data_state"] == "unknown"

    # A current probe that checked and found no data directories says "none".
    current = client.post(
        "/api/nodes/runtimes",
        json={
            "node_id": "workstation",
            "runtimes": [{"runtime": "codex", "command": "codex"}],
            "data_dirs": [],
        },
        headers=headers,
    )
    assert current.status_code == 200
    node = _node(client, "workstation")
    assert node["data_dirs_probed_at"] is not None
    assert node["runtimes"][0]["data_state"] == "none"


def test_data_dir_signal_matches_what_the_local_scan_shows(client):
    _login(client)
    headers = _node_headers(client, "workstation")

    # Two runtimes in use with no CLI on PATH: data directories only.
    response = client.post(
        "/api/nodes/runtimes",
        json={
            "node_id": "workstation",
            "runtimes": [],
            "data_dirs": [
                {"runtime": "codex", "path_hint": "~/.codex/sessions",
                 "last_changed_at": "2026-08-01T10:00:00+00:00"},
                {"runtime": "claude-code", "path_hint": "~/.claude/projects"},
            ],
        },
        headers=headers,
    )
    assert response.status_code == 200

    node = _node(client, "workstation")
    # Local history counts as found: this is not an empty inventory.
    assert node["runtime_state"] == "probed_found"
    entries = {item["runtime"]: item for item in node["runtimes"]}
    assert entries["codex"]["available"] is False
    assert entries["codex"]["data_state"] == "present"
    assert entries["codex"]["path_hint"] == "~/.codex/sessions"
    assert entries["codex"]["data_changed_at"] is not None
    assert entries["claude-code"]["path_hint"] == "~/.claude/projects"

    # CLI and history together: both signals on one row.
    response = client.post(
        "/api/nodes/runtimes",
        json={
            "node_id": "workstation",
            "runtimes": [{"runtime": "codex", "command": "codex", "source": "path"}],
            "data_dirs": [{"runtime": "codex", "path_hint": "~/.codex/sessions"}],
        },
        headers=headers,
    )
    assert response.status_code == 200
    entries = {item["runtime"]: item for item in _node(client, "workstation")["runtimes"]}
    assert entries["codex"]["available"] is True
    assert entries["codex"]["data_state"] == "present"
    # claude-code vanished from the report: a current probe's absence is a fact.
    assert entries["claude-code"]["data_state"] == "none"
    assert entries["claude-code"]["path_hint"] is None


def test_absolute_path_never_reaches_the_server(client, tmp_path, monkeypatch):
    _login(client)
    headers = _node_headers(client, "workstation")

    for bad_hint in ("/var/lib/agent/.codex", "C:\\agent\\.codex", "../outside"):
        response = client.post(
            "/api/nodes/runtimes",
            json={
                "node_id": "workstation",
                "runtimes": [],
                "data_dirs": [{"runtime": "codex", "path_hint": bad_hint}],
            },
            headers=headers,
        )
        assert response.status_code == 422, bad_hint
    assert client.get("/api/nodes/workstation/runtimes").json() == []

    # The collector itself only ever emits tilde-relative hints.
    import node.runtime_probe as runtime_probe

    home = tmp_path / "synthetic-home"
    (home / ".codex" / "sessions").mkdir(parents=True)
    (home / ".claude").mkdir(parents=True)
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))
    monkeypatch.setattr(runtime_probe.shutil, "which", lambda command, path=None: None)

    payload = runtime_probe.collect("workstation")
    text = json.dumps(payload)
    assert str(home) not in text
    assert {entry["path_hint"] for entry in payload["data_dirs"]} == {
        "~/.codex/sessions",
        "~/.claude",
    }


def test_inventory_columns_migrate_additively_with_baseline(tmp_path):
    db_path = tmp_path / "fleet.db"
    make_session_factory(db_path)
    # Simulate a deployment from before this change: the columns are gone and
    # the stored version predates the migration.
    raw = sqlite3.connect(db_path)
    raw.execute("ALTER TABLE nodes DROP COLUMN runtimes_probed_at")
    raw.execute("ALTER TABLE nodes DROP COLUMN data_dirs_probed_at")
    raw.execute("ALTER TABLE node_runtimes DROP COLUMN path_hint")
    raw.execute("ALTER TABLE node_runtimes DROP COLUMN data_changed_at")
    raw.execute("UPDATE schema_version SET version = 4 WHERE id = 1")
    raw.commit()
    raw.close()

    migrate_database(db_path)
    factory = make_session_factory(db_path)

    engine = factory.kw["bind"]
    node_columns = {column["name"] for column in inspect(engine).get_columns("nodes")}
    runtime_columns = {
        column["name"] for column in inspect(engine).get_columns("node_runtimes")
    }
    assert {"runtimes_probed_at", "data_dirs_probed_at"} <= node_columns
    assert {"path_hint", "data_changed_at"} <= runtime_columns
    with factory() as db:
        version = db.execute(
            select(SchemaVersion.version).where(SchemaVersion.id == 1)
        ).scalar_one()
    assert version == LATEST_SCHEMA_VERSION
