"""Execution attempts remain visible without becoming task state."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from server.app import create_app
from server.db import Actor, ApiToken, Node, NodeToken, User, make_session_factory
from server.engine import create_task
from server.security import hash_password, hash_token


ACTOR_BEARER = "actor-bearer-for-tests"
NODE_BEARER = "node-bearer-for-tests"


@pytest.fixture()
def attempt_env(tmp_path):
    factory = make_session_factory(tmp_path / "attempts.db")
    with factory() as db:
        db.add_all(
            [
                Actor(
                    id="duty-agent",
                    kind="agent",
                    display_name="Duty Agent",
                    node="managed-node",
                ),
                Actor(
                    id="remote-agent",
                    kind="agent",
                    display_name="Remote Agent",
                    node="other-node",
                ),
            ]
        )
        db.flush()
        db.add(
            User(
                username="operator",
                password_hash=hash_password("operator-pass-123"),
                role="admin",
                actor_id="duty-agent",
            )
        )
        db.add(
            ApiToken(
                token_hash=hash_token(ACTOR_BEARER),
                actor_id="duty-agent",
                label="attempt reporter",
            )
        )
        db.add(
            NodeToken(
                token_hash=hash_token(NODE_BEARER),
                node_id="managed-node",
                label="duty reporter",
            )
        )
        db.add(Node(id="managed-node", admitted_by="test-operator"))
        task = create_task(
            db,
            title="Verify managed duty",
            created_by="duty-agent",
            holder="duty-agent",
        )
        remote_task = create_task(
            db,
            title="Verify remote duty",
            created_by="remote-agent",
            holder="remote-agent",
        )
        task_id = task.id
        remote_task_id = remote_task.id
        db.commit()
    return TestClient(create_app(factory)), task_id, remote_task_id


def actor_headers() -> dict[str, str]:
    return {"Authorization": f"Bearer {ACTOR_BEARER}"}


def node_headers() -> dict[str, str]:
    return {"Authorization": f"Bearer {NODE_BEARER}"}


def attempt_body(
    key: str,
    *,
    outcome: str = "failed",
    reason: str | None = "authentication rejected",
    exit_status: int | None = 77,
) -> dict[str, object]:
    return {
        "outcome": outcome,
        "started_at": "2026-08-09T02:00:00+00:00",
        "ended_at": "2026-08-09T02:00:04+00:00",
        "reason": reason,
        "exit_status": exit_status,
        "idempotency_key": key,
    }


def test_failed_attempt_is_visible_without_changing_task_state(attempt_env):
    client, task_id, _ = attempt_env
    before = client.get(f"/api/tasks/{task_id}", headers=actor_headers()).json()

    reported = client.post(
        f"/api/tasks/{task_id}/attempts",
        json=attempt_body("attempt:event-0001"),
        headers=actor_headers(),
    )

    assert reported.status_code == 200
    assert reported.json()["task_status"] == "queued"
    after = client.get(f"/api/tasks/{task_id}", headers=actor_headers()).json()
    assert after["status"] == before["status"] == "queued"
    assert after["holder"] == before["holder"] == "duty-agent"
    assert after["updated_at"] == before["updated_at"]
    assert after["chain"] == before["chain"]
    assert after["attempts"] == [reported.json()["attempt"]]
    assert after["attempts"][0]["outcome"] == "failed"
    assert after["attempts"][0]["reason"] == "authentication rejected"
    assert after["attempts"][0]["exit_status"] == 77


def test_many_attempts_are_retained_in_append_order(attempt_env):
    client, task_id, _ = attempt_env
    headers = actor_headers()
    reports = [
        attempt_body("attempt:event-0011"),
        attempt_body(
            "attempt:event-0012",
            outcome="cancelled",
            reason="superseded by operator",
            exit_status=None,
        ),
        attempt_body(
            "attempt:event-0013",
            outcome="succeeded",
            reason=None,
            exit_status=None,
        ),
    ]
    ids = []
    for body in reports:
        response = client.post(
            f"/api/tasks/{task_id}/attempts", json=body, headers=headers
        )
        assert response.status_code == 200
        ids.append(response.json()["attempt"]["id"])

    attempts = client.get(
        f"/api/tasks/{task_id}/attempts", headers=headers
    ).json()
    assert [attempt["seq"] for attempt in attempts] == [1, 2, 3]
    assert [attempt["id"] for attempt in attempts] == ids
    assert [attempt["outcome"] for attempt in attempts] == [
        "failed",
        "cancelled",
        "succeeded",
    ]

    replay = client.post(
        f"/api/tasks/{task_id}/attempts", json=reports[0], headers=headers
    )
    assert replay.status_code == 200
    assert replay.json()["created"] is False
    assert len(client.get(f"/api/tasks/{task_id}/attempts", headers=headers).json()) == 3


def test_node_duty_uses_node_credential_without_actor_attribution(attempt_env):
    client, task_id, remote_task_id = attempt_env
    node_body = {**attempt_body("attempt:node-0001"), "duty": "session-index"}

    reported = client.post(
        f"/api/nodes/managed-node/tasks/{task_id}/attempts",
        json=node_body,
        headers=node_headers(),
    )
    assert reported.status_code == 200
    assert reported.json()["attempt"]["reporter"] == {
        "kind": "node",
        "id": "managed-node",
        "duty": "session-index",
    }
    assert client.post(
        f"/api/tasks/{task_id}/attempts",
        json=attempt_body("attempt:node-0002"),
        headers=node_headers(),
    ).status_code == 401
    assert client.post(
        f"/api/nodes/managed-node/tasks/{remote_task_id}/attempts",
        json={**node_body, "idempotency_key": "attempt:node-0003"},
        headers=node_headers(),
    ).status_code == 403

    logged_in = client.post(
        "/api/auth/login",
        json={"username": "operator", "password": "operator-pass-123"},
    )
    assert logged_in.status_code == 200
    operator_report = client.post(
        f"/api/tasks/{task_id}/attempts",
        json=attempt_body("attempt:operator-0001"),
    )
    assert operator_report.status_code == 200
    assert operator_report.json()["attempt"]["reporter"] == {
        "kind": "operator",
        "id": "operator",
        "duty": None,
    }

    actor_report = client.post(
        f"/api/tasks/{task_id}/attempts",
        json=attempt_body("attempt:actor-0001"),
        headers=actor_headers(),
    )
    assert actor_report.status_code == 200
    assert actor_report.json()["attempt"]["reporter"]["kind"] == "actor"


@pytest.mark.parametrize(
    "unsafe_reason",
    [
        "state file unavailable at " + "/" + "var" + "/service/state",
        "authentication rejected: " + "token" + "=" + "not-for-storage",
    ],
)
def test_failure_reason_rejects_paths_and_credential_shapes(
    attempt_env, unsafe_reason
):
    client, task_id, _ = attempt_env
    response = client.post(
        f"/api/tasks/{task_id}/attempts",
        json=attempt_body("attempt:unsafe-0001", reason=unsafe_reason),
        headers=actor_headers(),
    )
    assert response.status_code == 422
    assert client.get(
        f"/api/tasks/{task_id}/attempts", headers=actor_headers()
    ).json() == []
