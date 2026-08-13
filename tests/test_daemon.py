import json

import yaml

from core.daemon import TaskDaemon
from core.protocol.task import create_task, update_task


def make_root(tmp_path):
    root = tmp_path / "fleet"
    (root / "tasks").mkdir(parents=True)
    (root / "nodes").mkdir()
    (root / "org.yaml").write_text(yaml.safe_dump({
        "org": "acme-inc",
        "departments": [{"id": "eng", "name": "Engineering"}],
        "agents": [
            {"id": "coder-1", "dept": "eng", "runtime": "local", "node": "node-1", "on_claim": ["agent-run", "--task"]},
            {"id": "writer-1", "dept": "eng", "runtime": "local", "node": "node-2", "on_claim": ["writer-run"]},
        ],
        "nodes": [{"id": "node-1"}, {"id": "node-2"}],
    }, sort_keys=False), encoding="utf-8")
    return root


def test_daemon_triggers_local_holder_and_persists_checkpoint(tmp_path):
    root = make_root(tmp_path)
    task = create_task(root / "tasks", task_id="task-20260719-101", title="Build", created_by="boss", holder="coder-1")
    calls = []
    daemon = TaskDaemon(root, "node-1", runner=lambda argv, env: calls.append((argv, env)))
    assert daemon.scan_once() == 1
    assert calls[0][0] == ["agent-run", "--task"]
    assert calls[0][1]["RETINUE_TASK_FILE"] == str(task)
    assert daemon.scan_once() == 0
    restarted = TaskDaemon(root, "node-1", runner=lambda argv, env: calls.append((argv, env)))
    assert restarted.scan_once() == 0
    state = json.loads((root / "nodes" / "node-1.daemon.json").read_text())
    assert state["task-20260719-101"] == 1


def test_daemon_ignores_nonlocal_and_non_claim_changes(tmp_path):
    root = make_root(tmp_path)
    other = create_task(root / "tasks", task_id="task-20260719-102", title="Write", created_by="boss", holder="writer-1")
    local = create_task(root / "tasks", task_id="task-20260719-103", title="Build", created_by="boss", holder="coder-1")
    calls = []
    daemon = TaskDaemon(root, "node-1", runner=lambda argv, env: calls.append(argv))
    assert daemon.scan_once() == 1
    update_task(local, status="doing", note="progress only")
    assert daemon.scan_once() == 0
    update_task(other, status="doing", holder="coder-1", note="handoff")
    assert daemon.scan_once() == 1
    assert len(calls) == 2


def test_daemon_ignores_task_embedded_commands(tmp_path):
    """Security negative case: docs/security.md#sec-2-hook-authority."""
    root = make_root(tmp_path)
    task_path = create_task(
        root / "tasks",
        task_id="task-20260719-104",
        title="Treat command-shaped text as data",
        created_by="boss",
        holder="coder-1",
        acceptance=["task command stays inert"],
    )
    task = yaml.safe_load(task_path.read_text(encoding="utf-8"))
    task["on_claim"] = ["task-injected", "--must-not-run"]
    task["command"] = "task-injected --must-not-run"
    task_path.write_text(yaml.safe_dump(task, sort_keys=False), encoding="utf-8")

    calls = []
    daemon = TaskDaemon(root, "node-1", runner=lambda argv, env: calls.append((argv, env)))

    assert daemon.scan_once() == 1
    assert calls[0][0] == ["agent-run", "--task"]
    assert "task-injected" not in " ".join(calls[0][0])
