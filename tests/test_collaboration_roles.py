"""Keep explicit session-index transports out of new model execution authority."""

import datetime as dt
from types import SimpleNamespace

import pytest
from sqlalchemy import func, select

from server.db import Actor, Task, TaskEvent
from server.discovery import is_sync_actor
from test_collaboration import (
    delegate, delegation_body, environment, headers, login_operator, policy, report, start,
)
from test_collaboration_context import opening


@pytest.mark.parametrize(("actor_id", "model", "runtime", "expected"), [
    ("agent-a", " Session-Index-V2 ", "codex", True),
    (" Agent-Session-Sync ", "", "codex", True),
    ("agent-a", "test-model-v1", "multi", False),
    ("agent-a", "", "multi", False),
    ("agent-a", "unknown", "codex", False),
    ("agent-a", "configured-at-runtime", "codex", False),
    ("session-worker", "session-assistant-v1", "multi", False),
])
def test_sync_detection_uses_only_explicit_identity_markers(actor_id, model, runtime, expected):
    actor = SimpleNamespace(id=actor_id, kind="agent", model=model, runtime=runtime,
                            display_name="会话 worker")
    assert is_sync_actor(actor) is expected


def mark_sync(factory, actor_id):
    with factory() as db:
        db.get(Actor, actor_id).model = " Session-Index-V2 "
        db.commit()


def test_sync_target_is_filtered_and_rejected_even_with_previous_policy_or_operator(environment):
    client, factory, root, _ = environment
    child = delegate(client, root)
    mark_sync(factory, "agent-b")
    view = client.get(f"/api/tasks/{root}/collaboration", headers=headers()).json()
    assert view["delegation_targets"] == ["agent-a"]
    assert view["delegation_policy"]["allowed_actor_ids"] == ["agent-a", "agent-b"]
    assert view["delegations"][0]["child_task_id"] == child
    denied = client.post(f"/api/tasks/{root}/delegations", headers=headers(),
                         json=delegation_body(key="new-target-001"))
    assert denied.status_code == 403 and "session sync" in denied.text
    login_operator(client)
    assert "agent-b" not in client.get(f"/api/tasks/{root}/collaboration").json()["delegation_targets"]
    assert policy(client, root, ["agent-a", "agent-b"]).status_code == 403
    assert client.post(f"/api/tasks/{root}/delegations", json=delegation_body(key="operator-target-001")).status_code == 403
    # The operator can remove an obsolete entry without rewriting its old grant.
    assert policy(client, root, ["agent-a"], key="remove-sync-001").status_code == 200
    with factory() as db:
        assert db.scalar(select(func.count()).select_from(Task)) == 3


def test_sync_holder_cannot_delegate_or_create_execution_even_for_operator(environment):
    client, factory, root, _ = environment
    mark_sync(factory, "agent-a")
    view = client.get(f"/api/tasks/{root}/collaboration", headers=headers()).json()
    assert view["can_delegate"] is False and view["delegation_targets"] == []
    assert client.post(f"/api/tasks/{root}/delegations", headers=headers(), json=delegation_body()).status_code == 403
    assert opening(client, root).status_code == 403
    login_operator(client)
    assert client.post(f"/api/tasks/{root}/runs", json={"title": "Work", "idempotency_key": "operator-run-001"}).status_code == 403
    assert client.get(f"/api/tasks/{root}/context").json()["runs"] == []


@pytest.mark.parametrize("model", ["", "unknown", "configured-at-runtime", "test-model-v1"])
def test_multi_and_incomplete_real_workers_remain_eligible(environment, model):
    client, factory, root, _ = environment
    with factory() as db:
        actor = db.get(Actor, "agent-b")
        actor.runtime, actor.model, actor.display_name = "multi", model, "会话 worker"
        db.commit()
    login_operator(client)
    assert policy(client, root, ["agent-a", "agent-b"]).status_code == 200
    assert "agent-b" in client.get(f"/api/tasks/{root}/collaboration", headers=headers()).json()["delegation_targets"]
    child = delegate(client, root)
    run = start(client, child)
    assert run["actor_id"] == "agent-b" and run["runtime"] == "multi"
    assert run["model"] == (model if model == "test-model-v1" else None)


@pytest.mark.parametrize("grant_before_reclassification", [False, True])
def test_reclassification_blocks_unconsumed_permission_and_prepared_start(environment, grant_before_reclassification):
    client, factory, root, _ = environment
    with factory() as db:
        task = db.get(Task, root)
        task.lease_term = 1
        task.lease_expires_at = dt.datetime.now(dt.timezone.utc) + dt.timedelta(minutes=10)
        db.commit()
    response = opening(client, root, lease_term=1, execution_state="prepared")
    assert response.status_code == 200, response.text
    run = response.json()["run"]
    url = f"/api/tasks/{root}/runs/{run['id']}/authorize-execution"
    if grant_before_reclassification:
        assert client.post(url, headers=headers(), json={"lease_term": 1, "request_key": "launch-role-001"}).json()["allow_execute"] is True
    mark_sync(factory, "agent-a")
    assert client.post(url, headers=headers(), json={"lease_term": 1, "request_key": "launch-role-002"}).status_code == 403
    result = report(client, root, run["id"], actor="agent-a", status="running", note="Runtime started",
                    execution_state="started", lease_term=1, idempotency_key="started-role-001")
    assert result.status_code == 403
    preserved = client.get(f"/api/tasks/{root}/context", headers=headers()).json()["runs"][0]
    assert preserved["execution_state"] == "prepared" and preserved["started_at"] is None
    with factory() as db:
        count = db.scalar(select(func.count()).select_from(TaskEvent).where(TaskEvent.event_type == "run_execution_authorized"))
        assert count == int(grant_before_reclassification)


def test_explicit_sync_id_cannot_receive_operator_delegation(environment):
    client, _, root, _ = environment
    login_operator(client)
    result = client.post("/api/actors", json={"id": "index-session-sync", "kind": "agent",
                                              "display_name": "Index transport", "runtime": "codex"})
    assert result.status_code == 200, result.text
    assert policy(client, root, ["agent-a", "index-session-sync"]).status_code == 403
    assert client.post(f"/api/tasks/{root}/delegations", json=delegation_body(delegated_to="index-session-sync")).status_code == 403
