"""Protocol-v1 contract tests for dispatch, workers, and the dashboard read model."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from server.app import create_app
from server.db import Actor, User, make_session_factory
from server.mcp_bridge import _summary, create_server
from server.security import hash_password


PIPELINE = [
    {"name": "Draft", "holder": "writer", "gate": "auto"},
    {"name": "Review", "holder": "checker", "gate": "review"},
    {"name": "Owner gate", "holder": "owner", "gate": "queen"},
]


@pytest.fixture()
def env(tmp_path):
    factory = make_session_factory(tmp_path / "dispatch.db")
    with factory() as db:
        db.add_all(
            [
                Actor(
                    id="owner",
                    kind="human",
                    display_name="Owner",
                    node="throne",
                ),
                Actor(
                    id="writer",
                    kind="agent",
                    display_name="Writer",
                    node="castle",
                ),
                Actor(
                    id="checker",
                    kind="agent",
                    display_name="Checker",
                    node="castle",
                ),
            ]
        )
        db.add(
            User(
                username="owner",
                password_hash=hash_password("owner-pass-123"),
                role="admin",
                actor_id="owner",
            )
        )
        db.commit()
    client = TestClient(create_app(factory))
    response = client.post(
        "/api/auth/login",
        json={"username": "owner", "password": "owner-pass-123"},
    )
    assert response.status_code == 200
    template = client.post(
        "/api/pipeline-templates",
        json={
            "name": "Content draft and review",
            "stages": PIPELINE,
            "match_terms": ["lesson", "handout", "slides"],
            "acceptance": ["artifact is reachable", "review outcome is recorded"],
        },
    )
    assert template.status_code == 200
    return client


def issue_token(client: TestClient, actor_id: str) -> dict[str, str]:
    response = client.post("/api/admin/tokens", json={"actor_id": actor_id})
    assert response.status_code == 200
    return {"Authorization": f"Bearer {response.json()['token']}"}


def dispatch(client: TestClient, key: str = "source:event-0001"):
    return client.post(
        "/api/dispatch",
        json={
            "intent": "Prepare a lesson handout and send it through review",
            "idempotency_key": key,
            "priority": "high",
        },
    )


def assert_two_line_receipt(body: dict) -> None:
    assert isinstance(body["receipt"], str)
    assert len(body["receipt"].splitlines()) == 2
    assert body["receipt"].startswith("【任务回执】")


def test_dispatch_is_deterministic_and_idempotent(env):
    client = env
    first = dispatch(client)
    assert first.status_code == 200
    body = first.json()
    assert body["created"] is True
    assert body["holder"] == "writer"
    assert body["status"] == "queued"
    assert body["pipeline_stage"] == 0
    assert body["matched_template"]["name"] == "Content draft and review"
    assert set(body["matched_terms"]) == {"lesson", "handout"}
    assert body["acceptance"] == [
        "artifact is reachable",
        "review outcome is recorded",
    ]
    assert_two_line_receipt(body)

    replay = dispatch(client)
    assert replay.status_code == 200
    assert replay.json()["created"] is False
    assert replay.json()["id"] == body["id"]
    assert len(client.get("/api/tasks").json()) == 1

    conflict = client.post(
        "/api/dispatch",
        json={
            "intent": "Prepare a different lesson",
            "idempotency_key": "source:event-0001",
            "priority": "high",
        },
    )
    assert conflict.status_code == 409
    no_match = client.post(
        "/api/dispatch",
        json={
            "intent": "Perform an unrelated operation",
            "idempotency_key": "source:event-0002",
        },
    )
    assert no_match.status_code == 422


def test_worker_start_progress_handoff_and_reject(env):
    client = env
    writer = issue_token(client, "writer")
    checker = issue_token(client, "checker")
    task_id = dispatch(client, "source:event-0100").json()["id"]

    started = client.post(
        f"/api/tasks/{task_id}/update",
        json={"status": "doing", "note": "started"},
        headers=writer,
    )
    assert started.status_code == 200
    assert_two_line_receipt(started.json())
    progress = client.post(
        f"/api/tasks/{task_id}/update",
        json={"progress": 60, "note": "draft ready for final pass"},
        headers=writer,
    )
    assert progress.status_code == 200
    assert progress.json()["progress"] == 60
    assert_two_line_receipt(progress.json())
    handed = client.post(
        f"/api/tasks/{task_id}/stage-done",
        json={"note": "draft complete", "confidence": 0.9},
        headers=writer,
    )
    assert handed.status_code == 200
    assert handed.json()["holder"] == "checker"
    assert handed.json()["status"] == "handoff"
    assert_two_line_receipt(handed.json())

    wrong_holder = client.post(
        f"/api/tasks/{task_id}/update",
        json={"status": "doing", "note": "wrong actor"},
        headers=writer,
    )
    assert wrong_holder.status_code == 403
    client.post(
        f"/api/tasks/{task_id}/update",
        json={"status": "doing", "note": "review started"},
        headers=checker,
    )
    rejected = client.post(
        f"/api/tasks/{task_id}/stage-reject",
        json={"note": "add one example"},
        headers=checker,
    )
    assert rejected.status_code == 200
    assert rejected.json()["holder"] == "writer"
    assert rejected.json()["pipeline_stage"] == 0
    assert rejected.json()["status"] == "handoff"
    assert_two_line_receipt(rejected.json())


def test_dashboard_is_read_only_retinue_projection(env, monkeypatch):
    client = env
    monkeypatch.setenv("RETINUE_DASHBOARD_TOKEN", "dashboard-test-token")
    task_id = dispatch(client, "source:event-0200").json()["id"]

    assert client.get("/api/dashboard/overview").status_code == 401
    response = client.get(
        "/api/dashboard/overview",
        headers={"x-retinue-dashboard-token": "dashboard-test-token"},
    )
    assert response.status_code == 200
    overview = response.json()
    assert overview["source"] == "retinue-api"
    assert len(overview["tasks"]) == 1
    task = overview["tasks"][0]
    assert task["id"] == task_id
    assert task["holder_display"] == "Writer"
    assert task["node"] == "castle"
    assert task["current_stage"]["name"] == "Draft"
    assert task["approval_status"] is None
    assert "chain" not in task
    assert "acceptance" not in task
    assert task["refs"] == []
    assert task["reviews"] == []


def test_review_comment_reply_are_idempotent_terminal_chain_events(env, monkeypatch):
    client = env
    monkeypatch.setenv("RETINUE_DASHBOARD_TOKEN", "dashboard-test-token")
    writer = issue_token(client, "writer")
    created = client.post(
        "/api/tasks",
        json={
            "title": "Review a delivered artifact",
            "holder": "writer",
            "acceptance": ["review thread is attached to this task"],
        },
    )
    assert created.status_code == 200
    task_id = created.json()["id"]
    assert client.post(
        f"/api/tasks/{task_id}/update",
        json={"status": "doing", "note": "artifact started"},
        headers=writer,
    ).status_code == 200
    assert client.post(
        f"/api/tasks/{task_id}/update",
        json={
            "refs": ["obsidian://open?vault=Internal&file=artifact"],
            "note": "artifact linked",
        },
        headers=writer,
    ).status_code == 200
    delivered = client.post(
        f"/api/tasks/{task_id}/update",
        json={"status": "done", "note": "artifact delivered"},
        headers=writer,
    )
    assert delivered.status_code == 200
    before = len(delivered.json()["chain"])

    comment_body = {
        "body": "Please bind this QC thread to the canonical task.",
        "artifact_ref": "obsidian://open?vault=Internal&file=artifact",
        "idempotency_key": "board:comment:0001",
    }
    comment = client.post(f"/api/tasks/{task_id}/reviews", json=comment_body)
    assert comment.status_code == 200
    assert comment.json()["created"] is True
    assert comment.json()["task_status"] == "done"
    review_id = comment.json()["review"]["id"]

    replay = client.post(f"/api/tasks/{task_id}/reviews", json=comment_body)
    assert replay.status_code == 200
    assert replay.json()["created"] is False
    assert replay.json()["review"]["id"] == review_id
    conflict = client.post(
        f"/api/tasks/{task_id}/reviews",
        json={**comment_body, "body": "different body"},
    )
    assert conflict.status_code == 409
    agent_comment = client.post(
        f"/api/tasks/{task_id}/reviews",
        json={**comment_body, "idempotency_key": "board:comment:agent"},
        headers=writer,
    )
    assert agent_comment.status_code == 403

    reply_body = {
        "body": "Accepted. The task card now projects the same review thread.",
        "decision": "accepted",
        "evidence_refs": ["https://example.test/evidence"],
        "idempotency_key": "agent:reply:0001",
    }
    reply = client.post(
        f"/api/tasks/{task_id}/reviews/{review_id}/replies",
        json=reply_body,
        headers=writer,
    )
    assert reply.status_code == 200
    assert reply.json()["created"] is True
    assert reply.json()["task_status"] == "done"
    assert reply.json()["review"]["decision"] == "accepted"
    assert reply.json()["review"]["evidence_refs"] == ["https://example.test/evidence"]
    assert client.post(
        f"/api/tasks/{task_id}/reviews/{review_id}/replies",
        json=reply_body,
        headers=writer,
    ).json()["created"] is False

    task = client.get(f"/api/tasks/{task_id}").json()
    assert task["status"] == "done"
    assert len(task["chain"]) == before + 2
    assert [event["type"] for event in task["chain"][-2:]] == [
        "review_comment",
        "review_reply",
    ]
    projected = client.get(
        "/api/dashboard/overview",
        headers={"x-retinue-dashboard-token": "dashboard-test-token"},
    ).json()["tasks"][0]
    assert projected["refs"] == ["obsidian://open?vault=Internal&file=artifact"]
    assert projected["reviews"][0]["id"] == review_id
    assert projected["reviews"][0]["decision"] == "accepted"


def test_throne_end_to_end_dispatch_to_owner_delivery(env, monkeypatch):
    client = env
    monkeypatch.setenv("RETINUE_DASHBOARD_TOKEN", "dashboard-test-token")
    writer = issue_token(client, "writer")
    checker = issue_token(client, "checker")
    created = dispatch(client, "source:event-0300")
    task_id = created.json()["id"]
    assert dispatch(client, "source:event-0300").json()["id"] == task_id

    assert client.post(
        f"/api/tasks/{task_id}/update",
        json={"status": "doing", "note": "draft started"},
        headers=writer,
    ).status_code == 200
    assert client.post(
        f"/api/tasks/{task_id}/stage-done",
        json={"note": "draft delivered"},
        headers=writer,
    ).status_code == 200
    assert client.post(
        f"/api/tasks/{task_id}/update",
        json={"status": "doing", "note": "review started"},
        headers=checker,
    ).status_code == 200
    gate = client.post(
        f"/api/tasks/{task_id}/stage-done",
        json={"note": "review passed"},
        headers=checker,
    )
    assert gate.status_code == 200
    assert gate.json()["holder"] == "owner"
    assert gate.json()["status"] == "handoff"

    projected = client.get(
        "/api/dashboard/overview",
        headers={"x-retinue-dashboard-token": "dashboard-test-token"},
    ).json()["tasks"][0]
    assert projected["current_stage"]["gate"] == "queen"
    assert projected["approval_status"] == "pending"

    approval = client.get(f"/api/approvals?pending=true&task_id={task_id}").json()[0]
    delivered = client.post(
        f"/api/approvals/{approval['id']}/decide",
        json={"decision": "approve", "note": "owner approved delivery"},
    )
    assert delivered.status_code == 200
    assert delivered.json()["status"] == "done"
    assert delivered.json()["progress"] == 100
    assert_two_line_receipt(delivered.json())


def test_mcp_worker_surface_and_dispatch_summary():
    names = set(create_server()._tool_manager._tools)
    assert {
        "dispatch_intent",
        "task_start",
        "task_renew",
        "task_progress",
        "task_attempt",
        "stage_done",
        "stage_reject",
    } <= names
    summary = _summary(
        {
            "id": "task-contract-001",
            "title": "Contract",
            "status": "queued",
            "holder": "writer",
            "chain": [],
            "receipt": "line one\nline two",
            "created": False,
            "idempotency_key": "source:event-0001",
            "matched_template": {"id": 1, "name": "Content"},
            "matched_terms": ["content"],
        }
    )
    assert summary["receipt"] == "line one\nline two"
    assert summary["created"] is False
    assert summary["idempotency_key"] == "source:event-0001"
    assert summary["matched_template"]["name"] == "Content"
