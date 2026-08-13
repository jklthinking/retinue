"""Outside-in proofs that publication reaches the board with its work chain."""

from __future__ import annotations

from io import StringIO
import json
import sys

from fastapi.testclient import TestClient
import yaml

from core.cli.main import main as cli_main
from core.panel import PanelApp
from core.protocol.task import load_task
from server.app import create_app
from server.db import Actor, User, make_session_factory
from server.security import hash_password


def call_file_panel(app: PanelApp, path: str) -> tuple[str, bytes]:
    statuses: list[str] = []
    body = b"".join(
        app(
            {"PATH_INFO": path, "REQUEST_METHOD": "GET"},
            lambda status, _headers: statuses.append(status),
        )
    )
    return statuses[0], body


def test_server_in_app_publication_to_agent_session_and_board(tmp_path):
    """The endpoints used by the board close one authenticated server loop."""
    factory = make_session_factory(tmp_path / "closed-loop.db")
    login_secret = "test-" + "only-value"
    with factory() as db:
        db.add_all(
            [
                Actor(id="operator", kind="human", display_name="Operator"),
                Actor(
                    id="worker-1",
                    kind="agent",
                    display_name="Worker 1",
                    runtime="codex",
                    node="local-node",
                ),
                Actor(id="other-agent", kind="agent", display_name="Other agent"),
                User(
                    username="operator",
                    password_hash=hash_password(login_secret),
                    role="admin",
                    actor_id="operator",
                ),
            ]
        )
        db.commit()

    client = TestClient(create_app(factory, data_dir=tmp_path))
    assert client.post(
        "/api/auth/login",
        json={"username": "operator", "password": login_secret},
    ).status_code == 200

    worker_token = client.post(
        "/api/admin/tokens", json={"actor_id": "worker-1", "label": "loop-test"}
    ).json()["token"]
    other_token = client.post(
        "/api/admin/tokens", json={"actor_id": "other-agent", "label": "loop-test"}
    ).json()["token"]
    worker_headers = {"Authorization": f"Bearer {worker_token}"}
    other_headers = {"Authorization": f"Bearer {other_token}"}

    # This is the same POST made by the in-app New Task dialog.
    published = client.post(
        "/api/tasks",
        json={
            "title": "Publish, work, and show the result",
            "holder": "worker-1",
            "priority": "high",
            "acceptance": ["the completed card and its chain appear on the board"],
        },
    )
    assert published.status_code == 200
    task_id = published.json()["id"]

    board = client.get("/api/tasks?page_size=100").json()
    assert [(item["id"], item["status"]) for item in board["items"]] == [
        (task_id, "queued")
    ]

    # A bearer-token principal cannot bypass the baton.
    denied = client.post(
        f"/api/tasks/{task_id}/update",
        json={"status": "doing", "note": "not my baton"},
        headers=other_headers,
    )
    assert denied.status_code == 403
    assert "holder-only-writes" in denied.json()["detail"]

    claimed = client.post(
        f"/api/tasks/{task_id}/update",
        json={"status": "doing", "note": "claimed through the agent API"},
        headers=worker_headers,
    )
    assert claimed.status_code == 200

    session = client.post(
        "/api/sessions/sync",
        json={
            "runtime": "codex",
            "external_id": "closed-loop-session",
            "title": "Closed-loop work record",
            "privacy": "metadata",
            "cursor": 1,
            "message_count": 2,
            "task_id": task_id,
        },
        headers=worker_headers,
    )
    assert session.status_code == 200
    session_id = session.json()["id"]

    recorded = client.post(
        f"/api/tasks/{task_id}/update",
        json={
            "progress": 80,
            "refs": [f"session:{session_id}", "artifact:result-note"],
            "note": "recorded the result and linked work session",
        },
        headers=worker_headers,
    )
    assert recorded.status_code == 200
    assert recorded.json()["progress"] == 80

    completed = client.post(
        f"/api/tasks/{task_id}/update",
        json={"status": "done", "note": "acceptance checked; work complete"},
        headers=worker_headers,
    )
    assert completed.status_code == 200

    # These are the board list and task-drawer projections, not ORM assertions.
    final_board = client.get("/api/tasks?page_size=100").json()["items"]
    card = next(item for item in final_board if item["id"] == task_id)
    assert card["status"] == "done"
    assert card["holder"] == "worker-1"
    assert card["progress"] == 100
    assert card["refs"] == [f"session:{session_id}", "artifact:result-note"]

    drawer = client.get(f"/api/tasks/{task_id}").json()
    assert [event["did"] for event in drawer["chain"]] == [
        "task created",
        "claimed through the agent API",
        "recorded the result and linked work session",
        "acceptance checked; work complete",
    ]
    assert [event["who"] for event in drawer["chain"]] == [
        "operator",
        "worker-1",
        "worker-1",
        "worker-1",
    ]
    visible_session = client.get("/api/sessions?actor_id=worker-1").json()
    assert visible_session[0]["task_id"] == task_id
    assert visible_session[0]["task_title"] == drawer["title"]


def test_file_cli_publication_to_receipt_and_panel(tmp_path, capsys):
    """The README-led offline commands produce the card and thread users see."""
    root = tmp_path / "retinue-local"
    task_id = "task-20300101-001"
    task_path = root / "tasks" / f"{task_id}.yaml"

    assert cli_main(["init", str(root), "--org", "local-demo"]) == 0
    assert cli_main(
        [
            "task",
            "new",
            str(root / "tasks"),
            "--id",
            task_id,
            "--title",
            "Publish, work, and show the result",
            "--created-by",
            "operator",
            "--holder",
            "worker-1",
            "--priority",
            "high",
            "--acceptance",
            "the completed card and its chain appear on the board",
        ]
    ) == 0
    assert cli_main(
        [
            "task",
            "update",
            str(task_path),
            "--status",
            "doing",
            "--who",
            "worker-1",
            "--note",
            "claimed from the file bus",
        ]
    ) == 0
    assert cli_main(
        [
            "task",
            "update",
            str(task_path),
            "--ref",
            "artifact:result-note",
            "--who",
            "worker-1",
            "--note",
            "recorded the result reference",
        ]
    ) == 0
    assert cli_main(
        [
            "task",
            "update",
            str(task_path),
            "--status",
            "done",
            "--who",
            "worker-1",
            "--note",
            "acceptance checked; work complete",
        ]
    ) == 0
    assert cli_main(["receipt", str(task_path)]) == 0
    assert cli_main(["task", "lint", str(root / "tasks")]) == 0

    output = capsys.readouterr().out
    assert "状态：doing → done" in output
    assert "备注：acceptance checked; work complete" in output

    status, board = call_file_panel(PanelApp(root), "/")
    assert status == "200 OK"
    assert task_id.encode() in board
    assert b"Publish, work, and show the result" in board

    status, thread = call_file_panel(PanelApp(root), f"/tasks/{task_id}")
    assert status == "200 OK"
    assert b"claimed from the file bus" in thread
    assert b"recorded the result reference" in thread
    assert b"acceptance checked; work complete" in thread

    status, raw_projection = call_file_panel(PanelApp(root), f"/api/tasks/{task_id}")
    assert status == "200 OK"
    projected = json.loads(raw_projection)
    assert projected["status"] == "done"
    assert projected["refs"] == ["artifact:result-note"]
    assert len(projected["chain"]) == 4


def test_feishu_stdin_receipts_progress_existing_card_to_panel(
    tmp_path, monkeypatch, capsys
):
    """Credential-free stdin events exercise the supported IM relay path."""
    root = tmp_path / "retinue-im"
    assert cli_main(["init", str(root), "--org", "local-demo"]) == 0
    assert cli_main(
        [
            "task",
            "new",
            str(root / "tasks"),
            "--id",
            "task-20300101-002",
            "--title",
            "Relayed IM task",
            "--created-by",
            "operator",
            "--holder",
            "worker-1",
            "--note",
            "published on the canonical file bus",
        ]
    ) == 0
    org = {
        "org": "local-demo",
        "departments": [{"id": "work", "name": "Work"}],
        "agents": [
            {
                "id": "worker-1",
                "dept": "work",
                "runtime": "local",
                "node": "local-node",
            }
        ],
        "nodes": [{"id": "local-node"}],
        "adapters": {
            "feishu": {
                "enabled": True,
                "chat_id_env": "RETINUE_TEST_CHAT",
                "profile_env": "RETINUE_TEST_PROFILE",
                "self_mention_id_env": "RETINUE_TEST_SELF",
                "allow_from_env": "RETINUE_TEST_SENDERS",
            }
        },
    }
    (root / "org.yaml").write_text(
        yaml.safe_dump(org, sort_keys=False), encoding="utf-8"
    )
    monkeypatch.setenv("RETINUE_TEST_CHAT", "offline-chat")
    monkeypatch.setenv("RETINUE_TEST_PROFILE", "offline-profile")
    monkeypatch.setenv("RETINUE_TEST_SELF", "self-placeholder")
    monkeypatch.setenv("RETINUE_TEST_SENDERS", "sender-placeholder")

    def receive(old: str, new: str, note: str) -> None:
        receipt = (
            "【任务回执】task-20300101-002 Relayed IM task\n"
            f"状态：{old} → {new}　持棒：worker-1 → worker-1　备注：{note}"
        )
        event = {
            "event": {
                "sender": {"sender_id": {"open_id": "sender-placeholder"}},
                "message": {
                    "message_id": f"message-{new}",
                    "content": json.dumps({"text": receipt}, ensure_ascii=False),
                    "mentions": [{"id": {"open_id": "self-placeholder"}}],
                },
            }
        }
        monkeypatch.setattr(sys, "stdin", StringIO(json.dumps(event)))
        assert cli_main(["feishu", "receive", str(root)]) == 0

    receive("queued", "doing", "claimed from the addressed IM receipt")
    receive("queued", "doing", "claimed from the addressed IM receipt")
    receive("doing", "done", "result recorded and acceptance checked")

    assert capsys.readouterr().out.splitlines()[-3:] == [
        "applied",
        "duplicate",
        "applied",
    ]
    card = load_task(root / "tasks" / "task-20300101-002.yaml")
    assert card["status"] == "done"
    assert [event["did"] for event in card["chain"][-2:]] == [
        "claimed from the addressed IM receipt",
        "result recorded and acceptance checked",
    ]

    status, board = call_file_panel(PanelApp(root), "/")
    assert status == "200 OK"
    assert b"Relayed IM task" in board
    status, thread = call_file_panel(
        PanelApp(root), "/tasks/task-20300101-002"
    )
    assert status == "200 OK"
    assert b"claimed from the addressed IM receipt" in thread
    assert b"result recorded and acceptance checked" in thread
