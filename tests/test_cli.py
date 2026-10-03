from core.cli.main import main
from core.protocol.task import load_task


def test_cli_end_to_end(tmp_path, capsys):
    root = tmp_path / "demo"
    assert main(["init", str(root), "--org", "acme-inc"]) == 0
    assert main(
        [
            "task", "new", str(root / "tasks"),
            "--id", "task-20260719-007",
            "--title", "Run checks",
            "--created-by", "boss",
            "--holder", "tester-1",
            "--priority", "high",
            "--acceptance", "tests pass",
            "--at", "2026-07-19T11:00+08:00",
        ]
    ) == 0
    path = root / "tasks" / "task-20260719-007.yaml"
    assert main(
        [
            "task", "update", str(path),
            "--status", "doing",
            "--note", "started checks",
            "--at", "2026-07-19T11:05+08:00",
        ]
    ) == 0
    assert main(["task", "show", str(path)]) == 0
    assert main(["task", "audit", str(path)]) == 0
    assert main(["task", "lint", str(root / "tasks")]) == 0
    assert main(["receipt", str(path)]) == 0
    assert [event["at"] for event in load_task(path)["chain"]] == [
        "2026-07-19T03:00:00.000000Z",
        "2026-07-19T03:05:00.000000Z",
    ]
    output = capsys.readouterr().out
    assert "OK" in output
    assert "状态：queued → doing" in output
    assert "priority: high" in output
    assert "acceptance:" in output
    assert '"status": "in_sync"' in output


def test_cli_invalid_transition_returns_error(tmp_path, capsys):
    root = tmp_path / "tasks"
    main(
        [
            "task", "new", str(root),
            "--id", "task-20260719-008",
            "--title", "Run checks",
            "--created-by", "boss",
            "--holder", "tester-1",
        ]
    )
    path = root / "task-20260719-008.yaml"
    assert main(
        ["task", "update", str(path), "--status", "done", "--note", "skip"]
    ) == 2
    assert "illegal status transition" in capsys.readouterr().err


def test_live_sessions_cli_lists_verified_locations(tmp_path, monkeypatch, capsys):
    token_file = tmp_path / "actor.token"
    token_file.write_text("actor-secret", encoding="utf-8")
    monkeypatch.setattr(
        "core.cli.live.list_live_sessions",
        lambda **_kwargs: [
            {
                "bound_live_session_id": "live-20260902-001",
                "actor_id": "worker-one",
                "runtime": "codex",
                "state": "idle",
                "node_id": "node-d",
                "display_location": "work:1.0",
                "backend": "tmux",
                "generation": "a" * 32,
            }
        ],
    )

    assert main(["sessions", "--token-file", str(token_file)]) == 0
    output = capsys.readouterr().out
    assert "live-20260902-001\tworker-one\tcodex\tidle\tnode-d:work:1.0" in output


def test_live_control_cli_forwards_fenced_request(tmp_path, monkeypatch, capsys):
    token_file = tmp_path / "actor.token"
    token_file.write_text("actor-secret", encoding="utf-8")
    seen = {}

    def fake_create(live_session_id, verb, **kwargs):
        seen.update({"live_session_id": live_session_id, "verb": verb, **kwargs})
        return {"id": "ctl-" + "a" * 24, "status": "queued"}

    monkeypatch.setattr("core.cli.live.create_control", fake_create)
    assert main(
        [
            "tell",
            "live-20260902-001",
            "请同步进度",
            "--idempotency-key",
            "source:event-1001",
            "--url",
            "http://hub.invalid",
            "--token-file",
            str(token_file),
        ]
    ) == 0
    assert seen == {
        "live_session_id": "live-20260902-001",
        "verb": "tell",
        "url": "http://hub.invalid",
        "token": "actor-secret",
        "idempotency_key": "source:event-1001",
        "message": "请同步进度",
        "lines": None,
    }
    assert '"status": "queued"' in capsys.readouterr().out


def test_live_cli_reports_missing_token_without_traceback(capsys):
    assert main(["sessions"]) == 2
    assert "requires --token-file" in capsys.readouterr().err
