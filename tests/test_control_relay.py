from __future__ import annotations

import subprocess

from node import control_relay, session_probe


def _result(stdout="", returncode=0):
    return subprocess.CompletedProcess(
        args=["tmux"], returncode=returncode, stdout=stdout, stderr=""
    )


def _row(**changes):
    values = {
        "session_id": "$1",
        "session_name": "work",
        "window_id": "@2",
        "window_index": "0",
        "window_name": "agents",
        "pane_id": "%4",
        "pane_index": "1",
        "pane_pid": "4242",
        "pane_created": "1700000000",
        "command": "codex",
        "cwd": "/synthetic/project",
        "pane_dead": "0",
        "retinue_session": "live-20260902-001",
        "retinue_actor": "worker-1",
        "retinue_runtime": "codex",
        "retinue_task": "task-20260902-001",
        "retinue_input_mode": "codex-prompt",
    }
    values.update(changes)
    return session_probe.FIELD_SEPARATOR.join(values.values())


class RelayTmux:
    def __init__(self):
        self.operations = []

    def __call__(self, argv, **kwargs):
        if "list-panes" in argv:
            return _result(_row() + "\n")
        self.operations.append((argv, kwargs.get("input")))
        if "capture-pane" in argv:
            private_path = "/".join(("", "home", "private", "project"))
            return _result(f"working in {private_path}\nBearer abcdefghijklmnop\nready\n")
        return _result()


def _envelope(verb="tell", **changes):
    probe = session_probe.collect("node-a", runner=RelayTmux())["panes"][0]
    value = {
        "id": "ctl-" + "a" * 24,
        "live_session_id": probe["live_session_id"],
        "node_id": "node-a",
        "backend": "tmux",
        "endpoint_id": probe["endpoint_id"],
        "generation": probe["generation"],
        "verb": verb,
        "payload": {"message": "请检查当前进度"} if verb == "tell" else {"lines": 20},
        "actor_id": probe["actor_id"],
        "task_id": probe["task_id"],
        "input_mode": probe["input_mode"],
        "tmux": probe["tmux"],
    }
    value.update(changes)
    return value


def test_tell_uses_buffer_then_exact_pane_enter():
    tmux = RelayTmux()
    result = control_relay.execute(_envelope(), "node-a", runner=tmux)

    assert result["outcome"] == "delivered"
    commands = [argv[1] for argv, _input in tmux.operations]
    assert commands == ["load-buffer", "paste-buffer", "send-keys"]
    assert tmux.operations[0][1] == "请检查当前进度"
    assert tmux.operations[-1][0][-1] == "Enter"


def test_generation_mismatch_fails_before_any_control_operation():
    tmux = RelayTmux()
    envelope = _envelope(generation="f" * 32)

    result = control_relay.execute(envelope, "node-a", runner=tmux)

    assert result["outcome"] == "failed"
    assert "generation" in result["detail"]
    assert tmux.operations == []


def test_tell_without_current_input_contract_fails_closed():
    tmux = RelayTmux()
    envelope = _envelope(input_mode="")

    result = control_relay.execute(envelope, "node-a", runner=tmux)

    assert result["outcome"] == "failed"
    assert "input-mode" in result["detail"]
    assert tmux.operations == []


def test_peek_is_bounded_and_redacted_on_node():
    tmux = RelayTmux()
    result = control_relay.execute(_envelope("peek"), "node-a", runner=tmux)

    assert result["outcome"] == "delivered"
    assert "[PRIVATE_PATH]" in result["result"]
    assert "[REDACTED]" in result["result"]
    assert "abcdefghijklmnop" not in result["result"]
    assert [argv[1] for argv, _input in tmux.operations] == ["capture-pane"]


def test_interrupt_sends_only_soft_control_key_to_exact_pane():
    tmux = RelayTmux()
    envelope = _envelope("interrupt", payload={})

    result = control_relay.execute(envelope, "node-a", runner=tmux)

    assert result["outcome"] == "delivered"
    assert len(tmux.operations) == 1
    argv, input_text = tmux.operations[0]
    assert argv[1:] == ["send-keys", "-t", "%4", "C-c"]
    assert input_text is None


def test_relay_acknowledges_failed_execution_without_retrying(monkeypatch):
    tmux = RelayTmux()
    envelope = _envelope(generation="f" * 32)
    acknowledgements = []
    monkeypatch.setattr(
        control_relay,
        "pull",
        lambda *_args, **_kwargs: [envelope],
    )

    def fake_ack(_url, _token, body):
        acknowledgements.append(body)
        return {"status": body["outcome"]}

    monkeypatch.setattr(control_relay, "ack", fake_ack)
    result = control_relay.relay_once(
        "http://hub.invalid", "token", "node-a", runner=tmux
    )

    assert result == [{"status": "failed"}]
    assert len(acknowledgements) == 1
    assert acknowledgements[0]["outcome"] == "failed"
    assert "generation" in acknowledgements[0]["detail"]
    assert tmux.operations == []
