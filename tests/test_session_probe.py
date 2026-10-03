from __future__ import annotations

import json
import subprocess

import pytest

from node import session_probe


def _result(stdout: str = "", stderr: str = "", returncode: int = 0):
    return subprocess.CompletedProcess(
        args=["tmux"], returncode=returncode, stdout=stdout, stderr=stderr
    )


def _row(**changes: str) -> str:
    values = {
        "session_id": "$1",
        "session_name": "work",
        "window_id": "@2",
        "window_index": "3",
        "window_name": "agents",
        "pane_id": "%4",
        "pane_index": "1",
        "pane_pid": "4242",
        "pane_created": "1700000000",
        "command": "codex",
        "cwd": "/synthetic/workspace/project",
        "pane_dead": "0",
        "retinue_session": "",
        "retinue_actor": "",
        "retinue_runtime": "",
        "retinue_task": "",
        "retinue_input_mode": "",
    }
    values.update(changes)
    return session_probe.FIELD_SEPARATOR.join(values.values())


def test_missing_tmux_is_an_empty_read_only_inventory():
    def missing(*args, **kwargs):
        raise FileNotFoundError

    payload = session_probe.collect("node-a", runner=missing)

    assert payload["available"] is False
    assert payload["status"] == "unavailable"
    assert payload["panes"] == []


def test_no_running_server_is_not_a_probe_failure():
    def no_server(*args, **kwargs):
        return _result(
            stderr="error connecting to /synthetic/socket (No such file or directory)",
            returncode=1,
        )

    payload = session_probe.collect("node-a", runner=no_server)

    assert payload["available"] is True
    assert payload["status"] == "no-server"
    assert "/synthetic/socket" not in json.dumps(payload)


def test_process_detection_stays_unknown_and_reports_no_absolute_path():
    def runner(*args, **kwargs):
        return _result(stdout=_row() + "\n")

    payload = session_probe.collect(
        "node-a", socket_path="/synthetic/private/tmux.sock", runner=runner
    )
    pane = payload["panes"][0]

    assert pane["runtime"] == "codex"
    assert pane["state"] == "unknown"
    assert pane["state_source"] == "process"
    assert pane["state_confidence"] == 60
    assert pane["binding_source"] == "process"
    assert pane["control_eligible"] is False
    assert pane["cwd_hint"] == "project"
    rendered = json.dumps(payload)
    assert "/synthetic/private" not in rendered
    assert "/synthetic/workspace" not in rendered


def test_complete_explicit_metadata_and_matching_occupant_mark_control_eligible():
    def runner(*args, **kwargs):
        return _result(
            stdout=_row(
                command="codex",
                retinue_session="live-20260902-001",
                retinue_actor="worker-1",
                retinue_runtime="codex",
                retinue_task="task-20260902-001",
                retinue_input_mode="codex-prompt",
            )
            + "\n"
        )

    pane = session_probe.collect("node-a", runner=runner)["panes"][0]

    assert pane["runtime"] == "codex"
    assert pane["actor_id"] == "worker-1"
    assert pane["live_session_id"] == "live-20260902-001"
    assert pane["task_id"] == "task-20260902-001"
    assert pane["input_mode"] == "codex-prompt"
    assert pane["binding_source"] == "explicit"
    assert pane["binding_confidence"] == 100
    assert pane["occupant_verified"] is True
    assert pane["control_eligible"] is True


def test_stale_explicit_metadata_cannot_authorize_a_different_occupant():
    def runner(*args, **kwargs):
        return _result(
            stdout=_row(
                command="bash",
                retinue_session="live-20260902-001",
                retinue_actor="worker-1",
                retinue_runtime="codex",
            )
            + "\n"
        )

    pane = session_probe.collect("node-a", runner=runner)["panes"][0]

    assert pane["runtime"] == "codex"
    assert pane["explicit_binding"] is True
    assert pane["binding_source"] == "explicit"
    assert pane["occupant_verified"] is False
    assert pane["state"] == "unknown"
    assert pane["state_source"] == "heuristic"
    assert pane["control_eligible"] is False


def test_dead_pane_is_disconnected_and_never_control_eligible():
    def runner(*args, **kwargs):
        return _result(
            stdout=_row(
                pane_dead="1",
                retinue_session="live-20260902-001",
                retinue_actor="worker-1",
                retinue_runtime="codex",
            )
            + "\n"
        )

    pane = session_probe.collect("node-a", runner=runner)["panes"][0]

    assert pane["state"] == "disconnected"
    assert pane["state_confidence"] == 100
    assert pane["control_eligible"] is False


def test_malformed_and_untrusted_tmux_rows_fail_closed():
    def runner(*args, **kwargs):
        stdout = "malformed\n" + _row(
            retinue_session="$(bad)",
            retinue_actor="Not A Slug",
            retinue_runtime="codex;sh",
        )
        return _result(stdout=stdout)

    payload = session_probe.collect("node-a", runner=runner)
    pane = payload["panes"][0]

    assert payload["ignored_rows"] == 1
    assert pane["explicit_binding"] is False
    assert pane["actor_id"] is None
    assert pane["live_session_id"] is None
    assert pane["control_eligible"] is False


def test_cli_sessions_is_local_json(monkeypatch, capsys):
    import node.cli

    monkeypatch.setattr(
        session_probe,
        "collect",
        lambda node_id, socket_path=None: {
            "node_id": node_id,
            "backend": "tmux",
            "available": True,
            "status": "ok",
            "panes": [],
        },
    )

    assert node.cli.main(["sessions", "--node", "node-a"]) == 0
    assert json.loads(capsys.readouterr().out)["node_id"] == "node-a"


def test_probe_rejects_non_slug_node_id_before_subprocess():
    def forbidden(*args, **kwargs):
        raise AssertionError("subprocess should not run")

    with pytest.raises(ValueError):
        session_probe.collect("Not A Slug", runner=forbidden)


class _TmuxHarness:
    def __init__(self, *, command: str = "codex"):
        self.command = command
        self.options = {
            "retinue_session": "",
            "retinue_actor": "",
            "retinue_runtime": "",
            "retinue_task": "",
            "retinue_input_mode": "",
        }
        self.updates: list[tuple[str, str | None]] = []

    def __call__(self, argv, **kwargs):
        if "list-panes" in argv:
            return _result(stdout=_row(command=self.command, **self.options) + "\n")
        if "set-option" not in argv:
            raise AssertionError(f"unexpected tmux operation: {argv}")
        option = next(item for item in argv if item.startswith("@retinue_"))
        key = option.removeprefix("@")
        if "-u" in argv:
            self.options[key] = ""
            self.updates.append((option, None))
        else:
            self.options[key] = argv[-1]
            self.updates.append((option, argv[-1]))
        return _result()


def test_bind_writes_metadata_without_input_and_runtime_is_commit_marker():
    harness = _TmuxHarness()

    pane = session_probe.bind(
        "node-a",
        pane_id="%4",
        actor_id="worker-1",
        live_session_id="live-20260902-001",
        runtime="codex",
        task_id="task-20260902-001",
        input_mode="codex-prompt",
        runner=harness,
    )

    assert pane["control_eligible"] is True
    assert pane["task_id"] == "task-20260902-001"
    assert pane["input_mode"] == "codex-prompt"
    assert harness.updates[-1] == ("@retinue_runtime", "codex")
    assert all(name != "send-keys" for name, _value in harness.updates)


def test_bind_refuses_mismatched_occupant_before_writing_options():
    harness = _TmuxHarness(command="bash")

    with pytest.raises(session_probe.SessionProbeError, match="occupant"):
        session_probe.bind(
            "node-a",
            pane_id="%4",
            actor_id="worker-1",
            live_session_id="live-20260902-001",
            runtime="codex",
            runner=harness,
        )

    assert harness.updates == []


def test_unbind_removes_runtime_first_and_fails_closed_afterward():
    harness = _TmuxHarness()
    harness.options.update(
        retinue_session="live-20260902-001",
        retinue_actor="worker-1",
        retinue_runtime="codex",
        retinue_task="task-20260902-001",
    )

    pane = session_probe.unbind("node-a", pane_id="%4", runner=harness)

    assert harness.updates[0] == ("@retinue_runtime", None)
    assert pane["explicit_binding"] is False
    assert pane["control_eligible"] is False


def test_push_uses_inward_http_class_and_node_probe_route(monkeypatch):
    captured = {}

    class Response:
        def close(self):
            captured["closed"] = True

    def fake_open(request, **kwargs):
        captured["request"] = request
        captured.update(kwargs)
        return Response()

    monkeypatch.setattr(session_probe, "open_url", fake_open)
    session_probe.push(
        "http://hub.invalid/",
        "node-secret",
        {"node_id": "node-a", "backend": "tmux", "panes": []},
    )

    request = captured["request"]
    assert request.full_url == "http://hub.invalid/api/live-sessions/probe"
    assert request.get_header("Authorization") == "Bearer node-secret"
    assert captured["request_class"] is session_probe.RequestClass.INWARD
    assert captured["closed"] is True
