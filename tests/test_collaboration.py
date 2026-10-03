"""Real delegation, execution evidence, fencing and retry capability boundaries."""

from __future__ import annotations

import datetime as dt

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from core.protocol.task import ProtocolError
from server.app import create_app
from server.collaboration import set_delegation_policy
from server.collaboration_schemas import DelegationPolicyBody
from server.deps import Principal
from server.db import Actor, ApiToken, Task, TaskEvent, User, make_session_factory
from server.engine import create_task
from server.security import hash_password, hash_token


@pytest.fixture()
def environment(tmp_path):
    factory = make_session_factory(tmp_path / "collaboration.db")
    with factory() as db:
        for actor_id, kind in [("agent-a", "agent"), ("agent-b", "agent"), ("reviewer", "human")]:
            db.add(Actor(id=actor_id, kind=kind, display_name=actor_id))
        db.flush()
        for actor_id in ("agent-a", "agent-b"):
            db.add(ApiToken(token_hash=hash_token(f"synthetic-{actor_id}-bearer"), actor_id=actor_id, label="test"))
        db.add(User(username="operator", password_hash=hash_password("synthetic-password"), role="admin", actor_id="reviewer"))
        db.add(User(username="observer", password_hash=hash_password("synthetic-password"), role="viewer", actor_id="reviewer"))
        task = create_task(db, title="Draft shared guide", created_by="agent-a", holder="agent-a", acceptance=["Review passes"])
        other = create_task(db, title="Independent work", created_by="agent-b", holder="agent-b")
        set_delegation_policy(db, task, Principal(kind="user", name="operator", actor_id="reviewer", role="admin"), DelegationPolicyBody(allowed_actor_ids=["agent-a", "agent-b"], idempotency_key="test-policy-0001"))
        root_id, other_id = task.id, other.id
        db.commit()
    return TestClient(create_app(factory)), factory, root_id, other_id


def headers(actor="agent-a"):
    return {"Authorization": f"Bearer synthetic-{actor}-bearer"}


def delegation_body(key="delegate-0001", **overrides):
    return {"delegated_to": "agent-b", "title": "Check sources", "instruction": "Verify the cited facts", "acceptance": ["Every claim has a source"], "idempotency_key": key, **overrides}


def delegate(client, root_id):
    response = client.post(f"/api/tasks/{root_id}/delegations", headers=headers(), json=delegation_body())
    assert response.status_code == 200, response.text
    return response.json()["delegation"]["child_task_id"]


def start(client, task_id, actor="agent-b", key="run-start-0001"):
    result = client.post(f"/api/tasks/{task_id}/update", headers=headers(actor), json={"status": "doing", "note": "Execution begins"})
    assert result.status_code == 200, result.text
    result = client.post(f"/api/tasks/{task_id}/runs", headers=headers(actor), json={"title": "Check references", "idempotency_key": key})
    assert result.status_code == 200, result.text
    return result.json()["run"]


def report(client, task_id, run_id, actor="agent-b", **body):
    return client.post(f"/api/tasks/{task_id}/runs/{run_id}/events", headers=headers(actor), json=body)


def attempt(client, task_id, run, actor="agent-b", outcome="succeeded"):
    body = {"outcome": outcome, "started_at": run["started_at"], "ended_at": dt.datetime.now(dt.timezone.utc).isoformat(), "idempotency_key": f"attempt-{run['id']}", "lease_term": run["lease_term"] or None}
    if outcome == "failed":
        body["reason"] = "Reference service unavailable"
    result = client.post(f"/api/tasks/{task_id}/attempts", headers=headers(actor), json=body)
    assert result.status_code == 200, result.text
    return result.json()["attempt"]["id"]


def test_delegation_creates_separately_held_card_and_idempotent_relation(environment):
    client, factory, root, _ = environment
    child = delegate(client, root)
    again = client.post(f"/api/tasks/{root}/delegations", headers=headers(), json=delegation_body())
    assert again.status_code == 200 and again.json()["created"] is False
    changed = client.post(f"/api/tasks/{root}/delegations", headers=headers(), json=delegation_body(title="Different scope"))
    assert changed.status_code == 409
    parent_data = client.get(f"/api/tasks/{root}", headers=headers()).json()
    child_data = client.get(f"/api/tasks/{child}", headers=headers()).json()
    assert parent_data["holder"] == "agent-a"
    assert child_data["holder"] == "agent-b"
    assert child_data["status"] == "queued"
    assert len(parent_data["chain"]) == 3
    assert len(child_data["chain"]) == 1
    assert client.get(f"/api/tasks/{root}/drift", headers=headers()).json()["status"] == "in_sync"
    assert client.get(f"/api/tasks/{child}/drift", headers=headers()).json()["status"] == "in_sync"
    view = client.get(f"/api/tasks/{root}/collaboration", headers=headers()).json()
    assert [d["child_task_id"] for d in view["delegations"]] == [child]
    assert view["runs"] == [] and view["coverage"] == {"instrumented_tasks": 0, "total_tasks": 2}
    assert view["can_delegate"] is True
    with factory() as db:
        assert db.scalar(select(func.count()).select_from(Task)) == 3


def test_non_holder_cannot_delegate_or_report_peer_runs(environment):
    client, _, root, _ = environment
    assert client.post(f"/api/tasks/{root}/delegations", headers=headers("agent-b"), json=delegation_body()).status_code == 403
    child = delegate(client, root)
    run = start(client, child)
    rejected = report(client, child, run["id"], actor="agent-a", status="running", note="Not mine", idempotency_key="wrong-holder-01")
    assert rejected.status_code == 403
    assert client.get(f"/api/tasks/{root}/collaboration", headers=headers("agent-b")).json()["can_delegate"] is False


def test_nested_delegation_keeps_root_and_selected_task(environment):
    client, _, root, _ = environment
    child = delegate(client, root)
    result = client.post(f"/api/tasks/{child}/delegations", headers=headers("agent-b"), json=delegation_body(key="nested-child-01", delegated_to="agent-a"))
    assert result.status_code == 200, result.text
    view = client.get(f"/api/tasks/{child}/collaboration", headers=headers()).json()
    assert view["task_id"] == child and view["root_task_id"] == root
    assert len(view["tasks"]) == 3 and len(view["delegations"]) == 2


def test_run_progress_wait_resume_and_attempt_link_do_not_complete_task(environment):
    client, _, root, _ = environment
    child = delegate(client, root)
    run = start(client, child)
    assert run["model"] is None and run["model_source"] == "unknown"
    duplicate = client.post(f"/api/tasks/{child}/runs", headers=headers("agent-b"), json={"title": "Check references", "idempotency_key": "run-start-0001"})
    assert duplicate.status_code == 200 and duplicate.json()["created"] is False
    waiting = {"status": "waiting", "note": "Waiting for review", "idempotency_key": "waiting-0001", "waiting": {"kind": "review", "owner": "reviewer", "reason": "Source selection needs review"}, "progress": {"completed": 1, "total": 3, "unit": "checks"}}
    result = report(client, child, run["id"], **waiting)
    assert result.status_code == 200, result.text
    since = result.json()["run"]["waiting"]["since"]
    repeated = report(client, child, run["id"], **waiting)
    assert repeated.json()["created"] is False
    resumed = report(client, child, run["id"], status="running", note="Review received", idempotency_key="resume-0001")
    assert resumed.status_code == 200 and resumed.json()["run"]["waiting"] is None
    assert resumed.json()["run"]["progress"]["completed"] == 1
    assert since
    attempt_id = attempt(client, child, run)
    done_body = {"status": "succeeded", "note": "References checked", "idempotency_key": "done-0001", "attempt_id": attempt_id, "refs": ["artifact:source-list"]}
    done = report(client, child, run["id"], **done_body)
    assert done.status_code == 200, done.text
    assert done.json()["run"]["attempt_id"] == attempt_id
    assert report(client, child, run["id"], **done_body).json()["created"] is False
    assert report(client, child, run["id"], status="running", note="Reopen", idempotency_key="reopen-0001").status_code == 422
    task = client.get(f"/api/tasks/{child}", headers=headers()).json()
    assert task["status"] == "doing" and task["progress"] == 0
    view = client.get(f"/api/tasks/{root}/collaboration", headers=headers()).json()
    assert view["coverage"]["instrumented_tasks"] == 1
    assert view["runs"][0]["refs"] == ["artifact:source-list"]


def test_start_before_doing_and_parallel_run_are_rejected(environment):
    client, _, root, _ = environment
    response = client.post(f"/api/tasks/{root}/runs", headers=headers(), json={"title": "Actual work", "idempotency_key": "queued-start-01"})
    assert response.status_code == 422
    start(client, root, actor="agent-a")
    response = client.post(f"/api/tasks/{root}/runs", headers=headers(), json={"title": "Parallel work", "idempotency_key": "parallel-start-01"})
    assert response.status_code == 409


def test_terminal_run_requires_matching_same_task_attempt(environment):
    client, _, root, other = environment
    run = start(client, root, actor="agent-a")
    assert report(client, root, run["id"], actor="agent-a", status="succeeded", note="Done", idempotency_key="no-attempt-001").status_code == 422
    other_run = start(client, other)
    other_attempt = attempt(client, other, other_run)
    assert report(client, root, run["id"], actor="agent-a", status="succeeded", note="Done", idempotency_key="other-attempt-01", attempt_id=other_attempt).status_code == 422
    failed = attempt(client, root, run, actor="agent-a", outcome="failed")
    assert report(client, root, run["id"], actor="agent-a", status="succeeded", note="Done", idempotency_key="wrong-outcome-01", attempt_id=failed).status_code == 422


def test_replaced_lease_fences_old_run_even_without_term(environment):
    client, _, root, _ = environment
    run = start(client, root, actor="agent-a")
    result = client.post(f"/api/tasks/{root}/retry", headers=headers(), json={"note": "New execution lease"})
    assert result.status_code == 200, result.text
    assert report(client, root, run["id"], actor="agent-a", status="running", note="Stale executor", idempotency_key="stale-report-01").status_code == 409
    result = client.post(f"/api/tasks/{root}/runs", headers=headers(), json={"title": "New execution", "idempotency_key": "new-lease-run-01"})
    assert result.status_code == 200
    view = client.get(f"/api/tasks/{root}/collaboration", headers=headers()).json()
    assert view["runs"][0]["lease_current"] is False
    assert view["runs"][1]["lease_current"] is True


def test_expired_and_explicit_stale_leases_are_rejected(environment):
    client, factory, root, _ = environment
    run = start(client, root, actor="agent-a")
    with factory() as db:
        task = db.get(Task, root)
        task.lease_expires_at = dt.datetime.now(dt.timezone.utc) - dt.timedelta(seconds=5)
        db.commit()
    assert report(client, root, run["id"], actor="agent-a", status="running", note="Late report", idempotency_key="expired-report-01", lease_term=run["lease_term"]).status_code == 409
    assert client.post(f"/api/tasks/{root}/delegations", headers=headers(), json=delegation_body()).status_code == 409
    expired = client.get(f"/api/tasks/{root}/collaboration", headers=headers()).json()["runs"][0]
    assert expired["lease_current"] is True and expired["lease_live"] is False


@pytest.mark.parametrize("field,value", [("instruction", "password=synthetic-value"), ("instruction", "Run --unsafe"), ("instruction", "Read /private/source"), ("instruction", "line one\nline two"), ("acceptance", [" "])])
def test_delegation_rejects_untrusted_ledger_content(environment, field, value):
    client, _, root, _ = environment
    response = client.post(f"/api/tasks/{root}/delegations", headers=headers(), json=delegation_body(**{field: value}))
    assert response.status_code == 422


def test_incremental_cursors_include_new_children_and_separate_attempts(environment):
    client, _, root, _ = environment
    initial = client.get(f"/api/tasks/{root}/collaboration", headers=headers()).json()
    child = delegate(client, root)
    run = start(client, child)
    attempt_id = attempt(client, child, run)
    cursor, events, attempts = initial["cursor"], [], []
    while True:
        response = client.get(f"/api/tasks/{root}/collaboration/events", headers=headers(), params={"after_event_id": cursor["event_id"], "after_attempt_id": cursor["attempt_id"], "limit": 1})
        assert response.status_code == 200, response.text
        page = response.json()
        events.extend(page["events"])
        attempts.extend(page["attempts"])
        cursor = page["cursor"]
        if not page["has_more"]:
            break
    assert [e["id"] for e in events] == sorted({e["id"] for e in events})
    assert {e["task_id"] for e in events} == {root, child}
    assert [a["id"] for a in attempts] == [attempt_id]
    empty = client.get(f"/api/tasks/{root}/collaboration/events", headers=headers(), params={"after_event_id": cursor["event_id"], "after_attempt_id": cursor["attempt_id"]}).json()
    assert empty["events"] == [] and empty["attempts"] == [] and empty["cursor"] == cursor


def test_delegation_rolls_back_child_if_parent_event_fails(environment, monkeypatch):
    client, factory, root, _ = environment
    from server import collaboration
    def fail(*args, **kwargs):
        raise ProtocolError("synthetic parent write failed")
    monkeypatch.setattr(collaboration, "_event", fail)
    response = client.post(f"/api/tasks/{root}/delegations", headers=headers(), json=delegation_body())
    assert response.status_code == 422
    with factory() as db:
        assert db.scalar(select(func.count()).select_from(Task)) == 2
        assert db.scalar(select(func.count()).select_from(TaskEvent)) == 3


def test_retry_prepares_only_blocked_child_and_preserves_siblings(environment):
    client, _, root, _ = environment
    child = delegate(client, root)
    run = start(client, child)
    before = client.get(f"/api/tasks/{root}", headers=headers()).json()
    client.post(f"/api/tasks/{child}/update", headers=headers("agent-b"), json={"status": "blocked", "blocked_reason": "Service unavailable", "note": "Waiting on service"})
    view = client.get(f"/api/tasks/{root}/collaboration", headers=headers()).json()
    capability = next(item for item in view["recovery"] if item["task_id"] == child)
    assert capability["available"] is False
    assert client.post(f"/api/tasks/{child}/collaboration/retry", headers=headers(), json={"note": "Service restored"}).status_code == 403
    response = client.post(f"/api/tasks/{child}/collaboration/retry", headers=headers("agent-b"), json={"note": "Service restored"})
    assert response.status_code == 200, response.text
    assert response.json()["execution_started"] is False
    assert response.json()["task"]["status"] == "doing"
    assert response.json()["task"]["lease"]["term"] > run["lease_term"]
    assert client.get(f"/api/tasks/{root}", headers=headers()).json() == before
    assert client.post(f"/api/tasks/{child}/collaboration/retry", headers=headers("agent-b"), json={"note": "Duplicate retry"}).status_code == 422


def test_viewer_read_only_and_authentication_required(environment):
    client, _, root, _ = environment
    assert client.get(f"/api/tasks/{root}/collaboration").status_code == 401
    assert client.post("/api/auth/login", json={"username": "observer", "password": "synthetic-password"}).status_code == 200
    view = client.get(f"/api/tasks/{root}/collaboration")
    assert view.status_code == 200 and view.json()["can_delegate"] is False
    assert client.post(f"/api/tasks/{root}/delegations", json=delegation_body()).status_code == 403


def login_operator(client):
    result = client.post("/api/auth/login", json={"username": "operator", "password": "synthetic-password"})
    assert result.status_code == 200


def policy(client, root, actors, key="change-policy-0001", **limits):
    return client.post(f"/api/tasks/{root}/collaboration/policy", json={"allowed_actor_ids": actors, "idempotency_key": key, **limits})


def test_agent_cannot_grant_scope_and_revocation_preserves_existing_cards(environment):
    client, _, root, other = environment
    # This other root has no operator grant: an agent cannot name a peer.
    result = client.post(f"/api/tasks/{other}/delegations", headers=headers("agent-b"), json=delegation_body(delegated_to="agent-a"))
    assert result.status_code == 403
    result = client.post(f"/api/tasks/{root}/collaboration/policy", headers=headers(), json={"allowed_actor_ids": ["agent-a", "agent-b"], "idempotency_key": "agent-grant-0001"})
    assert result.status_code == 403
    child = delegate(client, root)
    login_operator(client)
    assert policy(client, root, []).status_code == 200
    denied = client.post(f"/api/tasks/{root}/delegations", headers=headers(), json=delegation_body(key="after-revoke-0001"))
    assert denied.status_code == 403
    view = client.get(f"/api/tasks/{child}/collaboration", headers=headers("agent-b")).json()
    assert len(view["delegations"]) == 1
    assert view["delegation_targets"] == ["agent-b"]
    assert view["can_manage_delegation_policy"] is False
    assert client.post(f"/api/tasks/{child}/collaboration/policy", json={"allowed_actor_ids": [], "idempotency_key": "child-policy-01"}).status_code == 422


def test_policy_requires_both_actors_and_never_grants_human_target(environment):
    client, _, root, _ = environment
    login_operator(client)
    assert policy(client, root, ["agent-b"]).status_code == 200
    denied = client.post(f"/api/tasks/{root}/delegations", headers=headers(), json=delegation_body())
    assert denied.status_code == 403
    assert policy(client, root, ["agent-a", "reviewer"], key="human-policy-01").status_code == 422
    assert client.post(f"/api/tasks/{root}/delegations", headers=headers(), json=delegation_body(delegated_to="reviewer")).status_code == 403
    # An actual operator retains the existing explicit human-dispatch permission.
    result = client.post(f"/api/tasks/{root}/delegations", json=delegation_body(delegated_to="reviewer"))
    assert result.status_code == 200, result.text


def test_policy_capacity_and_depth_are_enforced(environment):
    client, _, root, _ = environment
    login_operator(client)
    assert policy(client, root, ["agent-a", "agent-b"], max_children=2, max_depth=1).status_code == 200
    child = delegate(client, root)
    too_deep = client.post(f"/api/tasks/{child}/delegations", headers=headers("agent-b"), json=delegation_body(key="too-deep-0001", delegated_to="agent-a"))
    assert too_deep.status_code == 422
    second = client.post(f"/api/tasks/{root}/delegations", headers=headers(), json=delegation_body(key="second-child-01"))
    assert second.status_code == 200
    full = client.post(f"/api/tasks/{root}/delegations", headers=headers(), json=delegation_body(key="third-child-01"))
    assert full.status_code == 422
    assert client.get(f"/api/tasks/{root}/collaboration", headers=headers()).json()["can_delegate"] is False
    assert policy(client, root, ["agent-a"], key="excessive-depth", max_depth=4).status_code == 422
    assert policy(client, root, ["agent-a"], key="excessive-count", max_children=13).status_code == 422


def test_stale_sessions_cannot_start_two_runs_or_ignore_revoked_policy(environment):
    client, factory, root, _ = environment
    from server.collaboration import delegate as delegate_service, start_run
    from server.collaboration_schemas import DelegationBody, RunCreateBody
    from server.engine import Conflict, Forbidden
    client.post(f"/api/tasks/{root}/update", headers=headers(), json={"status": "doing", "note": "Start real work"})
    actor = Principal(kind="agent", name="agent-a", actor_id="agent-a", role="agent")
    operator = Principal(kind="user", name="operator", actor_id="reviewer", role="admin")
    with factory() as first, factory() as stale:
        first_task, stale_task = first.get(Task, root), stale.get(Task, root)
        assert len(first_task.events) == len(stale_task.events)
        start_run(first, first_task, actor, RunCreateBody(title="First execution", idempotency_key="first-execution-01"))
        first.commit()
        with pytest.raises(Conflict):
            start_run(stale, stale_task, actor, RunCreateBody(title="Second execution", idempotency_key="second-execution-01"))
        stale.rollback()
    with factory() as authority, factory() as stale:
        authority_task, stale_task = authority.get(Task, root), stale.get(Task, root)
        assert stale_task.events
        set_delegation_policy(authority, authority_task, operator, DelegationPolicyBody(allowed_actor_ids=[], idempotency_key="revoked-policy-01"))
        authority.commit()
        with pytest.raises(Forbidden):
            delegate_service(stale, stale_task, actor, DelegationBody(**delegation_body()))
        stale.rollback()


@pytest.mark.parametrize("field", ["idempotency_key", "session_ref"])
def test_run_report_identifiers_refuse_credential_shapes(environment, field):
    client, _, root, _ = environment
    client.post(f"/api/tasks/{root}/update", headers=headers(), json={"status": "doing", "note": "Start real work"})
    body = {"title": "Actual execution", "idempotency_key": "safe-run-key-0001", field: "sk-" + "examplevalue" * 3}
    result = client.post(f"/api/tasks/{root}/runs", headers=headers(), json=body)
    assert result.status_code == 422


def test_channel_credentials_cannot_read_collaboration_or_event_feed(environment):
    client, factory, root, _ = environment
    from server.db import ChannelToken
    with factory() as db:
        db.add(ChannelToken(token_hash=hash_token("synthetic-channel-bearer"), channel_id="demo-channel", label="test"))
        db.commit()
    channel = {"Authorization": "Bearer synthetic-channel-bearer"}
    for suffix in ("collaboration", "collaboration/events", "context"):
        assert client.get(f"/api/tasks/{root}/{suffix}", headers=channel).status_code == 403


def test_disabled_delegate_and_policy_target_are_rejected(environment):
    client, factory, root, _ = environment
    with factory() as db:
        db.get(Actor, "agent-b").disabled = True
        db.commit()
    assert client.post(f"/api/tasks/{root}/delegations", headers=headers(), json=delegation_body()).status_code == 422
    login_operator(client)
    assert policy(client, root, ["agent-a", "agent-b"]).status_code == 422
    assert "agent-b" not in client.get(f"/api/tasks/{root}/collaboration").json()["delegation_targets"]


def test_preloaded_sessions_cannot_consume_last_child_slot_twice(environment):
    client, factory, root, _ = environment
    from server.collaboration import delegate as delegate_service
    from server.collaboration_schemas import DelegationBody
    login_operator(client)
    assert policy(client, root, ["agent-a", "agent-b"], max_children=1).status_code == 200
    actor = Principal(kind="agent", name="agent-a", actor_id="agent-a", role="agent")
    with factory() as first, factory() as stale:
        task1, task2 = first.get(Task, root), stale.get(Task, root)
        assert task1.events and task2.events
        delegate_service(first, task1, actor, DelegationBody(**delegation_body()))
        first.commit()
        with pytest.raises(ProtocolError, match="上限"):
            delegate_service(stale, task2, actor, DelegationBody(**delegation_body(key="other-child-0001")))
        stale.rollback()
    assert len(client.get(f"/api/tasks/{root}/collaboration").json()["delegations"]) == 1


def test_attempt_without_lease_cannot_attest_a_new_run(environment):
    client, _, root, _ = environment
    run = start(client, root, actor="agent-a")
    result = client.post(f"/api/tasks/{root}/attempts", headers=headers(), json={"outcome": "succeeded", "started_at": run["started_at"], "ended_at": dt.datetime.now(dt.timezone.utc).isoformat(), "idempotency_key": "legacy-no-term-001"})
    assert result.status_code == 200
    assert report(client, root, run["id"], actor="agent-a", status="succeeded", note="Done", attempt_id=result.json()["attempt"]["id"], idempotency_key="legacy-link-001").status_code == 409


def test_mcp_tools_expose_reports_and_forward_lease_attempt_contract(monkeypatch):
    from server import mcp_bridge
    calls = []
    def capture(method, path, body=None):
        calls.append((method, path, body))
        return {"ok": True}
    monkeypatch.setattr(mcp_bridge, "_call", capture)
    tools = mcp_bridge.create_server()._tool_manager._tools
    assert {"task_collaboration", "task_delegate", "task_run_start", "task_run_report", "task_prepare_retry"} <= set(tools)
    assert "task_delegation_policy" not in tools
    tools["task_delegate"].fn(task_id="task-20260101-001", delegated_to="agent-b", title="Review", instruction="Review sources", acceptance=["Sources verified"], idempotency_key="mcp-delegate-01", lease_term=4)
    assert calls[-1][1].endswith("/delegations") and calls[-1][2]["lease_term"] == 4
    tools["task_run_start"].fn(task_id="task-20260101-001", title="Actual work", idempotency_key="mcp-run-0001", lease_term=4, session_ref="session-example")
    assert calls[-1][2]["session_ref"] == "session-example"
    tools["task_attempt"].fn(task_id="task-20260101-001", outcome="succeeded", started_at="2026-01-01T00:00:00Z", ended_at="2026-01-01T00:01:00Z", idempotency_key="mcp-attempt-01", lease_term=4)
    assert calls[-1][2]["lease_term"] == 4
    tools["task_run_report"].fn(task_id="task-20260101-001", run_id="run-example", status="succeeded", note="Sources verified", idempotency_key="mcp-report-01", lease_term=4, attempt_id="attempt-example")
    assert calls[-1][2]["attempt_id"] == "attempt-example"


def test_semantic_failure_can_close_evidence_without_renewing_expired_lease(environment):
    client, _, root, _ = environment
    child = delegate(client, root)
    run = start(client, child)
    failed = client.post(f"/api/tasks/{child}/attempts", headers=headers("agent-b"), json={"outcome": "failed", "reason": "Input meaning is invalid", "failure_class": "semantic", "started_at": run["started_at"], "ended_at": dt.datetime.now(dt.timezone.utc).isoformat(), "lease_term": run["lease_term"], "idempotency_key": "semantic-attempt-001"})
    assert failed.status_code == 200, failed.text
    before = client.get(f"/api/tasks/{child}", headers=headers()).json()
    assert before["status"] == "blocked" and before["lease"]["expires_at"] is None
    assert report(client, child, run["id"], status="running", note="Cannot resume", idempotency_key="semantic-resume-01").status_code == 409
    receipt = {"status": "failed", "note": "Invalid input recorded", "attempt_id": failed.json()["attempt"]["id"], "lease_term": run["lease_term"], "idempotency_key": "semantic-receipt-01"}
    result = report(client, child, run["id"], **receipt)
    assert result.status_code == 200, result.text
    assert result.json()["run"]["status"] == "failed"
    after = client.get(f"/api/tasks/{child}", headers=headers()).json()
    assert after["lease"] == before["lease"] and after["status"] == before["status"]
    assert len(after["chain"]) == len(before["chain"]) + 1
    assert after["attempts"] == before["attempts"]
    assert report(client, child, run["id"], **receipt).json()["created"] is False


def test_expired_terminal_receipt_still_rejects_old_holder_and_old_term(environment):
    client, _, root, _ = environment
    child = delegate(client, root)
    run = start(client, child)
    attempt_id = attempt(client, child, run, outcome="failed")
    client.post(f"/api/tasks/{child}/retry", headers=headers("agent-b"), json={"note": "New term"})
    body = {"status": "failed", "note": "Old execution ended", "attempt_id": attempt_id, "idempotency_key": "old-terminal-0001", "lease_term": run["lease_term"]}
    assert report(client, child, run["id"], **body).status_code == 409
    login_operator(client)
    client.post(f"/api/tasks/{child}/update", json={"holder": "agent-a", "note": "New holder"})
    assert report(client, child, run["id"], **body).status_code == 403


def test_feed_watermark_does_not_skip_child_created_during_tree_read(environment, monkeypatch):
    client, factory, root, _ = environment
    from server import collaboration
    from server.collaboration_schemas import DelegationBody
    login_operator(client)  # Cookie reads do not hold the bearer last-seen write lock.
    cursor = client.get(f"/api/tasks/{root}/collaboration").json()["cursor"]
    original = collaboration.task_tree
    dispatched = False
    def tree_then_delegate(db, task):
        nonlocal dispatched
        tree = original(db, task)
        if not dispatched:
            dispatched = True
            with factory() as writer:
                collaboration.delegate(writer, writer.get(Task, root), Principal(kind="agent", name="agent-a", actor_id="agent-a", role="agent"), DelegationBody(**delegation_body()))
                writer.commit()
        return tree
    monkeypatch.setattr(collaboration, "task_tree", tree_then_delegate)
    page1 = client.get(f"/api/tasks/{root}/collaboration/events", params={"after_event_id": cursor["event_id"], "after_attempt_id": cursor["attempt_id"]})
    assert page1.status_code == 200, page1.text
    assert page1.json()["events"] == []
    cursor = page1.json()["cursor"]
    page2 = client.get(f"/api/tasks/{root}/collaboration/events", params={"after_event_id": cursor["event_id"], "after_attempt_id": cursor["attempt_id"]}).json()
    assert {event["type"] for event in page2["events"]} == {"delegation", "collaboration_child"}
