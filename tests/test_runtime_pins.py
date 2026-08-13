"""Per-node executable pins: discovery, privacy, enrolment precedence, explain.

Every path here is synthetic, built under a temporary directory; nothing
touches the real home, the real PATH search results are pinned out, and no
pinned binary is ever executed — detection stays existence-only.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import node.cli
from node import enroll, runtime_pins, runtime_probe
from node.runtime_pins import ENV_PINS_FILE, RuntimePins
from server.app import create_app
from server.db import Actor, User, make_session_factory
from server.security import hash_password

URL = "http://127.0.0.1:9219"


def _make_executable(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("#!/bin/sh\n", encoding="utf-8")
    path.chmod(0o755)


def _write_pins(path: Path, data: dict) -> Path:
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


@pytest.fixture()
def isolated(tmp_path, monkeypatch):
    """A synthetic node: empty PATH, empty home, pin file under tmp."""
    empty_path = tmp_path / "empty-path"
    empty_path.mkdir()
    monkeypatch.setenv("PATH", str(empty_path))
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))
    monkeypatch.setenv(ENV_PINS_FILE, str(tmp_path / "pins.json"))
    monkeypatch.setattr("node.data_dirs.scan", lambda home=None: [])
    return tmp_path


# --- Discovery: a pin makes an otherwise-undiscoverable runtime available ---


def test_pin_makes_otherwise_undiscoverable_runtime_available(isolated):
    """The executable lives outside PATH and every well-known directory; only
    the pin can find it."""
    hidden = isolated / "hidden" / "bin" / "kimi"
    _make_executable(hidden)
    _write_pins(isolated / "pins.json", {"runtimes": {"kimi": str(hidden)}})

    payload = runtime_probe.collect("node-a")

    kimi = [item for item in payload["runtimes"] if item["runtime"] == "kimi"]
    assert kimi == [
        {
            "runtime": "kimi",
            "command": "kimi",
            "available": True,
            "source": "pin",
        }
    ]


def test_without_pin_the_same_layout_finds_nothing(isolated):
    _make_executable(isolated / "hidden" / "bin" / "kimi")

    payload = runtime_probe.collect("node-a")

    assert [item for item in payload["runtimes"] if item["runtime"] == "kimi"] == []


def test_missing_pinned_file_is_ignored_and_search_still_runs(isolated):
    _make_executable(isolated / "home" / ".local" / "bin" / "kimi")
    _write_pins(
        isolated / "pins.json",
        {"runtimes": {"kimi": str(isolated / "hidden" / "bin" / "kimi")}},
    )

    payload = runtime_probe.collect("node-a")

    kimi = [item for item in payload["runtimes"] if item["runtime"] == "kimi"]
    assert kimi == [
        {
            "runtime": "kimi",
            "command": "kimi",
            "available": True,
            "source": runtime_probe.SOURCE_WELL_KNOWN,
        }
    ]


# --- Privacy: a pinned path never travels ---


def test_payload_carries_basename_and_pin_label_never_the_path(isolated):
    hidden = isolated / "hidden" / "bin" / "kimi"
    _make_executable(hidden)
    _write_pins(isolated / "pins.json", {"runtimes": {"kimi": str(hidden)}})

    payload = runtime_probe.collect("node-a")
    text = json.dumps(payload)

    assert str(isolated) not in text
    assert str(hidden) not in text
    kimi = [item for item in payload["runtimes"] if item["runtime"] == "kimi"]
    assert kimi[0]["command"] == "kimi"  # basename only
    assert kimi[0]["source"] == "pin"


def test_runtime_pin_never_appears_in_rendered_units(isolated):
    """Rendered artifacts may contain only the interpreter (pinned or
    derived); a runtime pin is inventory configuration and never enters a
    unit destined for a server's review or a node's schedule."""
    hidden = isolated / "hidden" / "bin" / "kimi"
    _make_executable(hidden)
    _write_pins(isolated / "pins.json", {"runtimes": {"kimi": str(hidden)}})
    config = enroll.config_from_values(
        node="synthetic-node",
        url=URL,
        token_file="node-token",
        actor_token_file="",
        runtime="",
        source="",
        actor="",
        privacy="metadata",
        duty_keys=("heartbeat", "runtimes"),
    )
    for target in enroll.TARGETS:
        assert str(hidden) not in enroll.render(config, target)


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


def test_pinned_path_survives_no_hop_to_the_server(isolated, client):
    """The payload the node pushes, and every response the server later
    serves from it, carry the basename and the pin label — never the path."""
    hidden = isolated / "hidden" / "bin" / "kimi"
    _make_executable(hidden)
    _write_pins(isolated / "pins.json", {"runtimes": {"kimi": str(hidden)}})
    payload = runtime_probe.collect("workstation")
    assert str(hidden) not in json.dumps(payload)

    response = client.post(
        "/api/auth/login", json={"username": "owner", "password": "owner-pass-123"}
    )
    assert response.status_code == 200
    issued = client.post("/api/admin/node-tokens", json={"node_id": "workstation"})
    headers = {"Authorization": f"Bearer {issued.json()['token']}"}
    assert (
        client.post(
            "/api/nodes/heartbeat",
            json={"id": "workstation", "hostname": "synthetic-host"},
            headers=headers,
        ).status_code
        == 200
    )
    assert (
        client.post("/api/nodes/runtimes", json=payload, headers=headers).status_code
        == 200
    )

    stored = client.get("/api/nodes/workstation/runtimes")
    assert stored.status_code == 200
    assert str(hidden) not in json.dumps(stored.json())
    assert str(isolated) not in json.dumps(stored.json())
    kimi = [item for item in stored.json() if item["runtime"] == "kimi"]
    assert kimi[0]["command"] == "kimi"
    assert kimi[0]["source"] == "pin"


# --- Enrolment precedence: pin wins, derivation is the fallback ---

ENROLL_KWARGS = dict(
    node="synthetic-node",
    url=URL,
    token_file="node-token",
    actor_token_file="actor-token",
    runtime="codex",
    source="sessions",
    actor="agent-1",
    privacy="metadata",
    duty_keys=("heartbeat", "runtimes", "sessions"),
)


def test_enroll_falls_back_to_derivation_byte_exactly(isolated):
    config = enroll.config_from_values(**ENROLL_KWARGS, pins=RuntimePins())
    assert config.interpreter == sys.executable
    manual = enroll.EnrollConfig(**ENROLL_KWARGS, interpreter=sys.executable)
    for target in enroll.TARGETS:
        assert enroll.render(config, target) == enroll.render(manual, target)
    # The default pin lookup (file absent) derives the same way.
    defaulted = enroll.config_from_values(**ENROLL_KWARGS)
    assert defaulted.interpreter == sys.executable
    for target in enroll.TARGETS:
        assert enroll.render(defaulted, target) == enroll.render(manual, target)


def test_enroll_prefers_pinned_interpreter_byte_exactly(isolated):
    interpreter = isolated / "vm" / "bin" / "python3.11"
    _make_executable(interpreter)
    pinned = enroll.config_from_values(
        **ENROLL_KWARGS, pins=RuntimePins(interpreter=str(interpreter))
    )
    derived = enroll.config_from_values(**ENROLL_KWARGS, pins=RuntimePins())
    assert pinned.interpreter == str(interpreter)
    for target in enroll.TARGETS:
        # The pin changes exactly the interpreter token, nothing else.
        assert enroll.render(pinned, target) == enroll.render(derived, target).replace(
            sys.executable, str(interpreter)
        )
    units = dict(enroll.linux_files(pinned, Path("units")))
    heartbeat = units[Path("units") / "retinue-node-heartbeat.service"]
    assert f"ExecStart={interpreter} -m node.cli heartbeat\n" in heartbeat


def test_enroll_refuses_a_dead_interpreter_pin(isolated):
    dead = str(isolated / "vm" / "bin" / "python3.11")
    with pytest.raises(SystemExit) as excinfo:
        enroll.config_from_values(
            **ENROLL_KWARGS, pins=RuntimePins(interpreter=dead)
        )
    assert "pin" in str(excinfo.value)


# --- Explain mode: search locations only when asked ---


def test_explain_reveals_search_locations_only_when_asked(
    isolated, monkeypatch, capsys
):
    hidden = isolated / "hidden" / "bin" / "kimi"
    _make_executable(hidden)
    pins_path = _write_pins(
        isolated / "pins.json", {"runtimes": {"kimi": str(hidden)}}
    )
    pushed = []
    monkeypatch.setattr(runtime_probe, "push", lambda *a, **kw: pushed.append(a))
    token_file = isolated / "node-token"
    token_file.write_text("synthetic-token\n", encoding="utf-8")

    # Normal output: the summary line only — no location leaks.
    assert (
        node.cli.main(
            [
                "runtimes",
                "--node", "synthetic-node",
                "--url", URL,
                "--token-file", str(token_file),
            ]
        )
        == 0
    )
    normal = capsys.readouterr().out
    assert "Runtime inventory reported" in normal
    assert str(isolated) not in normal
    assert "pin file" not in normal
    assert len(pushed) == 1
    assert str(isolated) not in json.dumps(pushed[0][2])

    # Asked: every search location is named, and nothing is pushed.
    assert node.cli.main(["runtimes", "--explain"]) == 0
    explained = capsys.readouterr().out
    assert f"pin file: {pins_path}" in explained
    assert f"  pin: {hidden} (exists; used)" in explained
    assert "  PATH: codex: not found" in explained
    assert str(isolated / "home" / ".local" / "bin") in explained
    assert "  => available (source: pin)" in explained
    assert "node interpreter" in explained
    assert len(pushed) == 1  # explain mode pushes nothing


def test_explain_says_why_a_pin_was_not_used(isolated, capsys):
    dead = isolated / "hidden" / "bin" / "kimi"
    _write_pins(isolated / "pins.json", {"runtimes": {"kimi": str(dead)}})

    assert node.cli.main(["runtimes", "--explain"]) == 0
    explained = capsys.readouterr().out

    assert f"  pin: {dead} (missing; ignored)" in explained
    lines = explained.splitlines()
    header = lines.index("kimi:")
    section = []
    for line in lines[header + 1:]:
        if line and not line.startswith(" "):
            break
        section.append(line)
    assert "  => not found" in section


def test_explain_reports_pins_for_unknown_runtime_ids(isolated, capsys):
    _write_pins(
        isolated / "pins.json",
        {"runtimes": {"codx": str(isolated / "typo" / "codex")}},
    )

    assert node.cli.main(["runtimes", "--explain"]) == 0
    assert "pin for unknown runtime id ignored: codx" in capsys.readouterr().out


# --- Pin file loading and validation ---


def test_missing_pin_file_means_no_pins(isolated):
    assert runtime_pins.load() == RuntimePins()


def test_environment_override_selects_the_pin_file(isolated, monkeypatch):
    elsewhere = isolated / "elsewhere.json"
    monkeypatch.setenv(ENV_PINS_FILE, str(elsewhere))
    assert runtime_pins.pins_file() == elsewhere


def test_relative_pin_refused(isolated):
    path = _write_pins(isolated / "pins.json", {"runtimes": {"kimi": "bin/kimi"}})
    with pytest.raises(SystemExit) as excinfo:
        runtime_pins.load()
    assert "绝对路径" in str(excinfo.value)
    assert str(path) in str(excinfo.value)


def test_malformed_pin_file_refused(isolated):
    (isolated / "pins.json").write_text("{not json", encoding="utf-8")
    with pytest.raises(SystemExit) as excinfo:
        runtime_pins.load()
    assert "JSON" in str(excinfo.value)


def test_unknown_pin_key_refused(isolated):
    _write_pins(isolated / "pins.json", {"interpretre": "/x"})
    with pytest.raises(SystemExit) as excinfo:
        runtime_pins.load()
    assert "interpretre" in str(excinfo.value)


def test_default_pins_file_location(isolated, monkeypatch):
    monkeypatch.delenv(ENV_PINS_FILE)
    home = Path.home()
    assert (
        runtime_pins.default_pins_file(home, on_windows=False)
        == home / ".config" / "retinue" / "runtime-pins.json"
    )
    assert (
        runtime_pins.default_pins_file(home, on_windows=True)
        == home / "AppData" / "Roaming" / "retinue" / "runtime-pins.json"
    )
    # With no override, the conventional location is used.
    assert runtime_pins.pins_file() == runtime_pins.default_pins_file(
        home, runtime_pins.os.name == "nt"
    )


def test_render_stays_side_effect_free_with_pins(isolated):
    """Render with a pin consults the pin file but writes nothing."""
    interpreter = isolated / "vm" / "bin" / "python3.11"
    _make_executable(interpreter)
    _write_pins(isolated / "pins.json", {"interpreter": str(interpreter)})
    before = sorted(path.name for path in isolated.iterdir())
    config = enroll.config_from_values(**ENROLL_KWARGS)
    assert config.interpreter == str(interpreter)
    enroll.render(config, "linux-user")
    assert sorted(path.name for path in isolated.iterdir()) == before
