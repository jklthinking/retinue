"""Tests for ``retinue scan`` — the file-mode local machine scan."""

from __future__ import annotations

import io
import json
import sys
from pathlib import Path, PureWindowsPath

import core.cli.main as cli_main
import core.scan as scan_module
from core.cli.main import main
from core.protocol.task import ProtocolError
from core.scan import render_json, render_text, scan_machine
from node import data_dirs, runtime_probe

# The web framework, ASGI server, ORM, and crypto stack behind the server
# extra. The scan is a base-install feature, so these names are blocked
# during import rather than merely checked off an import list.
SERVER_STACK_MODULES = ("fastapi", "uvicorn", "sqlalchemy", "cryptography", "pydantic")


def _synthetic_machine(tmp_path: Path, monkeypatch):
    """A synthetic home and bin directory; nothing real is visible."""
    home = tmp_path / "home"
    home.mkdir()
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    monkeypatch.setenv("PATH", str(bin_dir))
    monkeypatch.setattr(
        runtime_probe,
        "_search_directories",
        lambda: [home / ".local" / "bin", home / "bin"],
    )
    return home, bin_dir


def _install_cli(bin_dir: Path, command: str) -> Path:
    executable = bin_dir / command
    executable.write_text("#!/bin/sh\nexit 0\n")
    executable.chmod(0o755)
    return executable


def _agent(report: dict, runtime: str) -> dict | None:
    return next(
        (agent for agent in report["agents"] if agent["runtime"] == runtime), None
    )


class _OutputBytes(io.BytesIO):
    def __init__(self, *, is_tty: bool) -> None:
        super().__init__()
        self._is_tty = is_tty

    def isatty(self) -> bool:
        return self._is_tty


def _scan_output(monkeypatch, *, encoding: str, is_tty: bool) -> tuple[bytes, str]:
    report = {
        "agents": [
            {
                "runtime": "codex",
                "label": "Codex",
                "cli": {"command": "codex", "available": True, "source": "path"},
                "data": None,
            }
        ],
        "summary": {"agents_found": 1, "with_cli": 1, "with_data": 0},
    }
    target = _OutputBytes(is_tty=is_tty)
    stream = io.TextIOWrapper(target, encoding=encoding, errors="strict")
    monkeypatch.setattr(sys, "stdout", stream)
    monkeypatch.setattr(scan_module, "scan_machine", lambda: report)

    assert main(["scan"]) == 0
    stream.flush()
    return target.getvalue(), render_text(report) + "\n"


def test_scan_runs_with_server_stack_unavailable(monkeypatch, capsys):
    for name in SERVER_STACK_MODULES:
        monkeypatch.setitem(sys.modules, name, None)
    assert main(["scan", "--json"]) == 0
    report = json.loads(capsys.readouterr().out)
    assert set(report) == {"agents", "summary"}
    assert set(report["summary"]) == {"agents_found", "with_cli", "with_data"}


def test_report_distinguishes_cli_only_from_data_only(tmp_path, monkeypatch):
    home, bin_dir = _synthetic_machine(tmp_path, monkeypatch)

    # Executable only: the CLI is on PATH but the machine has no history.
    _install_cli(bin_dir, "codex")
    report = scan_machine(home=home)
    codex = _agent(report, "codex")
    assert codex is not None
    assert codex["cli"] == {
        "command": "codex",
        "available": True,
        "source": "path",
    }
    assert codex["data"] is None
    text = render_text(report)
    assert "available (on PATH)" in text
    assert "no local history found" in text

    # Data only: the CLI is gone but the runtime's data directory remains.
    (bin_dir / "codex").unlink()
    (home / ".codex" / "sessions").mkdir(parents=True)
    report = scan_machine(home=home)
    codex = _agent(report, "codex")
    assert codex is not None
    assert codex["cli"] is None
    assert codex["data"]["path_hint"] == "~/.codex/sessions"
    text = render_text(report)
    assert "CLI:  not found" in text
    assert "~/.codex/sessions" in text


def test_report_marks_well_known_installs(tmp_path, monkeypatch):
    home, _ = _synthetic_machine(tmp_path, monkeypatch)
    install_dir = home / ".local" / "bin"
    install_dir.mkdir(parents=True)
    _install_cli(install_dir, "kimi")

    report = scan_machine(home=home)
    kimi = _agent(report, "kimi")
    assert kimi["cli"]["source"] == "well-known"
    assert "well-known install directory" in render_text(report)


def test_no_absolute_path_in_any_output_mode(tmp_path, monkeypatch, capsys):
    home, bin_dir = _synthetic_machine(tmp_path, monkeypatch)
    _install_cli(bin_dir, "kimi")
    (home / ".codex" / "sessions").mkdir(parents=True)
    monkeypatch.setattr(Path, "home", lambda: home)

    assert main(["scan"]) == 0
    text = capsys.readouterr().out
    assert main(["scan", "--json"]) == 0
    payload = capsys.readouterr().out

    for output in (text, payload):
        assert str(tmp_path) not in output
        assert str(home) not in output
        assert str(bin_dir) not in output
    report = json.loads(payload)
    assert report["summary"]["agents_found"] == 2
    assert render_json(report) == payload.rstrip("\n")


def test_scan_output_survives_gbk_console(monkeypatch):
    raw, expected = _scan_output(monkeypatch, encoding="gbk", is_tty=True)

    assert raw == expected.encode("gbk")
    assert raw.decode("gbk") == expected


def test_scan_output_keeps_utf8_appearance(monkeypatch):
    raw, expected = _scan_output(monkeypatch, encoding="utf-8", is_tty=True)

    assert raw == expected.encode("utf-8")
    assert "codex — available" in raw.decode("utf-8")


def test_scan_output_survives_legacy_redirection(monkeypatch):
    raw, expected = _scan_output(monkeypatch, encoding="cp437", is_tty=False)

    assert raw == expected.encode("cp437", errors="replace")
    assert "codex ? available" in raw.decode("cp437")


def test_cli_error_uses_legacy_stderr_fallback(monkeypatch):
    target = _OutputBytes(is_tty=True)
    stream = io.TextIOWrapper(target, encoding="cp437", errors="strict")
    monkeypatch.setattr(sys, "stderr", stream)

    def fail(_args):
        raise ProtocolError("invalid snowman: ☃")

    monkeypatch.setattr(cli_main, "run", fail)
    assert main(["scan"]) == 2
    stream.flush()
    assert target.getvalue().decode("cp437") == "error: invalid snowman: ?\n"


def test_path_hint_uses_tilde_and_forward_slashes_for_windows_paths():
    home = PureWindowsPath("synthetic-profile")
    candidate = home / ".codex" / "sessions"

    assert data_dirs._path_hint(candidate, home) == "~/.codex/sessions"


def test_empty_machine_says_so(tmp_path, monkeypatch):
    home, _ = _synthetic_machine(tmp_path, monkeypatch)
    report = scan_machine(home=home)
    assert report["agents"] == []
    assert report["summary"]["agents_found"] == 0
    assert "No agents found" in render_text(report)
