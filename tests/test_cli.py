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
