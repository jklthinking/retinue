import json
import os
import sys
from pathlib import Path, PurePosixPath, PureWindowsPath

from server.runtime_probe import (
    SOURCE_PATH,
    SOURCE_WELL_KNOWN,
    _well_known_directories,
    collect,
)


def _make_executable(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("#!/bin/sh\n", encoding="utf-8")
    path.chmod(0o755)


def test_runtime_probe_reports_only_available_command_basenames(monkeypatch):
    paths = {"codex": "/opt/bin/codex", "claude": "/opt/bin/claude"}

    def fake_which(command, path=None):
        # Only PATH lookups (path=None) resolve; nothing in fallback dirs.
        return paths.get(command) if path is None else None

    monkeypatch.setattr("server.runtime_probe.shutil.which", fake_which)
    # Pin the data-directory signal so the payload is machine-independent.
    monkeypatch.setattr("node.data_dirs.scan", lambda home=None: [])

    payload = collect("windows")

    assert payload == {
        "node_id": "windows",
        "runtimes": [
            {
                "runtime": "codex",
                "command": "codex",
                "available": True,
                "source": SOURCE_PATH,
            },
            {
                "runtime": "claude-code",
                "command": "claude",
                "available": True,
                "source": SOURCE_PATH,
            },
        ],
        "data_dirs": [],
    }
    assert all("/" not in item["command"] for item in payload["runtimes"])


def test_runtime_in_per_user_bin_only_is_still_reported(tmp_path, monkeypatch):
    """A runtime absent from PATH but present in a per-user bin dir is found.

    This is the P0-5 regression test: the scheduled probe's PATH is empty, so
    an implementation that only consults PATH reports nothing here.
    """
    empty_path = tmp_path / "empty-path"
    empty_path.mkdir()
    monkeypatch.setenv("PATH", str(empty_path))
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    _make_executable(tmp_path / ".local" / "bin" / "kimi")

    payload = collect("node-a")

    # Membership, not exact equality: the host may legitimately provide other
    # runtimes in the real global directories.
    kimi = [item for item in payload["runtimes"] if item["runtime"] == "kimi"]
    assert kimi == [
        {
            "runtime": "kimi",
            "command": "kimi",
            "available": True,
            "source": SOURCE_WELL_KNOWN,
        }
    ]


def test_payload_never_contains_absolute_paths(tmp_path, monkeypatch):
    path_dir = tmp_path / "on-path"
    _make_executable(path_dir / "codex")
    monkeypatch.setenv("PATH", str(path_dir))
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    _make_executable(tmp_path / ".local" / "bin" / "kimi")

    payload = collect("node-a")
    text = json.dumps(payload)

    assert str(tmp_path) not in text
    for item in payload["runtimes"]:
        command = item["command"]
        assert command == os.path.basename(command)
        assert not os.path.isabs(command)
        assert "/" not in command and "\\" not in command
        assert item["source"] in {SOURCE_PATH, SOURCE_WELL_KNOWN}


def test_windows_extension_variants_resolve_in_explicit_dirs(tmp_path, monkeypatch):
    """Extensionless lookups must still match PATHEXT variants off PATH."""
    empty_path = tmp_path / "empty-path"
    empty_path.mkdir()
    monkeypatch.setenv("PATH", str(empty_path))
    monkeypatch.setattr(sys, "platform", "win32")
    # _win_path_needs_curdir needs _winapi, which does not exist here. It is also
    # private and absent before 3.11, where shutil.which never calls it, so the stub
    # must be optional: the declared minimum interpreter is 3.10 and continuous
    # integration failed here on it.
    monkeypatch.setattr(
        "shutil._win_path_needs_curdir", lambda cmd, mode: False, raising=False
    )
    # PATHEXT is split with os.pathsep, which is ':' where these tests run.
    # Use lowercase variants so the case-sensitive test filesystem matches.
    monkeypatch.setenv("PATHEXT", ".exe:.cmd")
    monkeypatch.setattr(
        "server.runtime_probe._search_directories", lambda: [tmp_path / "ext-bin"]
    )
    _make_executable(tmp_path / "ext-bin" / "kimi.exe")

    payload = collect("node-a")

    assert payload["runtimes"] == [
        {
            "runtime": "kimi",
            "command": "kimi",
            "available": True,
            "source": SOURCE_WELL_KNOWN,
        }
    ]


def test_search_directory_order_posix():
    home = PurePosixPath("/syn/thetic")

    assert _well_known_directories(home, on_windows=False) == [
        PurePosixPath("/syn/thetic/.local/bin"),
        PurePosixPath("/syn/thetic/.cargo/bin"),
        PurePosixPath("/syn/thetic/bin"),
        PurePosixPath("/usr/local/bin"),
        PurePosixPath("/opt/homebrew/bin"),
    ]


def test_search_directory_order_windows():
    home = PureWindowsPath("C:/syn/thetic")

    assert _well_known_directories(home, on_windows=True) == [
        PureWindowsPath("C:/syn/thetic/.local/bin"),
        PureWindowsPath("C:/syn/thetic/.cargo/bin"),
        PureWindowsPath("C:/syn/thetic/AppData/Roaming/npm"),
        PureWindowsPath("C:/ProgramData/chocolatey/bin"),
        PureWindowsPath("C:/ProgramData/scoop/shims"),
    ]
