import json

import pytest
import yaml

from core.cli.main import main
from core.daemon import TaskDaemon
from core.demo import seed_sample
from core.panel import PanelApp
from core.protocol.task import ProtocolError, create_task, lint_path


def call(app, path):
    status = []
    body = b"".join(app(
        {"PATH_INFO": path, "REQUEST_METHOD": "GET"},
        lambda value, headers: status.append(value),
    ))
    return status[0], body


def test_seed_42_has_exact_mvp_shape_and_is_deterministic(tmp_path):
    first = seed_sample(tmp_path / "first", seed=42)
    second = seed_sample(tmp_path / "second", seed=42)
    org = yaml.safe_load((first / "org.yaml").read_text())
    assert len(org["nodes"]) == 1
    assert len(org["agents"]) == 3
    assert len(list((first / "tasks").glob("*.yaml"))) == 6
    assert len(json.loads((first / "nodes" / "laptop.daemon.json").read_text())) == 6
    assert all(error is None for _, error in lint_path(first / "tasks"))
    first_metrics = json.loads((first / "metrics" / "claude-1.json").read_text())
    second_metrics = json.loads((second / "metrics" / "claude-1.json").read_text())
    assert first_metrics == second_metrics
    assert len(first_metrics["last_7_days"]["daily"]) == 7

    status, body = call(PanelApp(first), "/overview")
    assert status == "200 OK"
    assert b"sample-seed-42" in body and body.count(b"class='agent'") == 3


def test_demo_command_seeds_without_serving(tmp_path, capsys):
    target = tmp_path / "demo"
    assert main(["demo", str(target), "--seed", "42", "--no-serve"]) == 0
    assert "Overview:" in capsys.readouterr().out
    with pytest.raises(ProtocolError, match="refusing to overwrite"):
        seed_sample(target, seed=42)


def test_demo_checkpoint_only_dispatches_new_cards(tmp_path):
    root = seed_sample(tmp_path / "demo", seed=42)
    calls = []
    daemon = TaskDaemon(root, "laptop", runner=lambda argv, env: calls.append((argv, env)))
    assert daemon.scan_once() == 0
    create_task(
        root / "tasks",
        task_id="task-20260720-901",
        title="Cold-start task",
        created_by="boss",
        holder="codex-1",
        acceptance=["task reaches done"],
    )
    assert daemon.scan_once() == 1
    assert calls[0][1]["RETINUE_TASK_ID"] == "task-20260720-901"
