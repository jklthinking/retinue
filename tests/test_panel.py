import json
from datetime import datetime

import yaml

from core.panel import PanelApp, overview_payload, render_overview
from core.protocol.task import create_task, update_task


def call(app, path, method="GET"):
    status = []
    headers = []
    body = b"".join(app({"PATH_INFO": path, "REQUEST_METHOD": method}, lambda s, h: (status.append(s), headers.extend(h))))
    return status[0], dict(headers), body


def test_panel_board_api_and_thread_are_read_only(tmp_path):
    root = tmp_path / "fleet"
    task = create_task(root / "tasks", task_id="task-20260719-108", title="Panel proof", created_by="boss", holder="coder-1", at="2026-07-19T10:00+08:00")
    update_task(task, status="doing", note="render timeline", at="2026-07-19T10:05+08:00")
    app = PanelApp(root)
    status, _, body = call(app, "/api/tasks")
    assert status == "200 OK"
    payload = json.loads(body)
    assert payload[0]["last_receipt_at"] == "2026-07-19T02:05:00.000000Z"
    status, _, body = call(app, "/")
    assert status == "200 OK"
    assert b"queued" in body and b"Panel proof" in body
    status, _, body = call(app, "/tasks/task-20260719-108")
    assert status == "200 OK"
    assert b"render timeline" in body and b"coder-1" in body


def test_panel_rejects_write_methods(tmp_path):
    """Security negative case: docs/security.md#sec-5-read-only-panel."""
    status, _, body = call(PanelApp(tmp_path), "/api/tasks", "POST")
    assert status == "405 Method Not Allowed"
    assert b"read-only" in body


def test_panel_exposes_ready_work_and_both_dependency_directions(tmp_path):
    root = tmp_path / "fleet"
    create_task(
        root / "tasks",
        task_id="task-20260809-011",
        title="Panel prerequisite",
        created_by="operator",
        holder="operator",
    )
    create_task(
        root / "tasks",
        task_id="task-20260809-012",
        title="Panel dependent",
        created_by="operator",
        holder="operator",
        depends_on=["task-20260809-011"],
    )
    app = PanelApp(root)

    status, _, body = call(app, "/api/tasks/ready")
    assert status == "200 OK"
    assert [task["id"] for task in json.loads(body)] == ["task-20260809-011"]

    status, _, body = call(app, "/")
    assert status == "200 OK"
    assert b"Ready work" in body and b"Blocked by" in body and b"Blocks" in body


def test_overview_merges_roster_and_metrics(tmp_path):
    root = tmp_path / "fleet"
    root.mkdir()
    (root / "org.yaml").write_text(yaml.safe_dump({
        "org": "acme-inc",
        "departments": [{"id": "eng", "name": "Engineering"}],
        "agents": [
            {"id": "claude-1", "dept": "eng", "runtime": "claude-code", "model": "opus", "node": "laptop"},
            {"id": "codex-1", "dept": "eng", "runtime": "codex", "model": "gpt", "node": "laptop"},
        ],
        "nodes": [{"id": "laptop"}],
    }), encoding="utf-8")
    (root / "metrics").mkdir()
    (root / "metrics" / "claude-1.json").write_text(json.dumps({
        "agent_id": "claude-1",
        "last_active_at": "2026-07-20T10:55:00+08:00",
        "today": {"total_tokens": 12500, "sessions": 3},
        "source": {"kind": "claude-code-jsonl"},
    }), encoding="utf-8")

    payload = overview_payload(
        root, now=datetime.fromisoformat("2026-07-20T11:00:00+08:00")
    )
    assert payload["today_tokens"] == 12500
    assert payload["online_agents"] == 1
    assert payload["agents"][0]["metrics_source"] == "claude-code-jsonl"
    assert payload["agents"][1]["today_tokens"] == 0
    body = render_overview(payload)
    assert b"claude-1" in body and b"12.5K" in body and b"opus" in body
    assert b"2026-07-20 10:55+08:00" in body


def test_overview_ignores_invalid_metric_counts(tmp_path):
    root = tmp_path / "fleet"
    root.mkdir()
    (root / "org.yaml").write_text(yaml.safe_dump({
        "org": "acme-inc",
        "departments": [{"id": "eng", "name": "Engineering"}],
        "agents": [{"id": "coder-1", "dept": "eng", "runtime": "codex", "node": "laptop"}],
        "nodes": [{"id": "laptop"}],
    }), encoding="utf-8")
    (root / "metrics").mkdir()
    (root / "metrics" / "coder-1.json").write_text(json.dumps({
        "agent_id": "coder-1",
        "today": {"total_tokens": "many", "sessions": -4},
        "last_active_at": 123,
        "source": {"kind": ["unexpected"]},
    }), encoding="utf-8")
    payload = overview_payload(root)
    assert payload["today_tokens"] == 0
    assert payload["agents"][0]["today_sessions"] == 0
    assert payload["agents"][0]["last_active_at"] is None
    assert payload["agents"][0]["metrics_source"] == "none"


def test_overview_routes_are_read_only(tmp_path):
    root = tmp_path / "fleet"
    (root / "tasks").mkdir(parents=True)
    (root / "org.yaml").write_text(yaml.safe_dump({
        "org": "acme-inc", "departments": [], "agents": [], "nodes": []
    }), encoding="utf-8")
    app = PanelApp(root)
    status, _, body = call(app, "/overview")
    assert status == "200 OK" and b"Agent overview" in body
    status, _, body = call(app, "/api/overview")
    assert status == "200 OK" and json.loads(body)["agents"] == []
