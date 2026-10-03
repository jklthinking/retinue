"""Synthetic integration checks for identity snapshots and private-safe handoffs."""

import datetime as dt
import json

import pytest
from sqlalchemy import select

from server import collaboration, mcp_bridge
from server.db import Actor, RuntimeSession, Task, TaskEvent
from server.deps import Principal
from test_collaboration import environment, headers, login_operator, report, start


def profile(factory):
    with factory() as db:
        actor = db.get(Actor, "agent-a")
        actor.node, actor.runtime, actor.model = "test-node", "test-runtime", "test-model-v1"
        db.commit()


def opening(client, task_id, **fields):
    result = client.post(f"/api/tasks/{task_id}/update", headers=headers(), json={"status": "doing", "note": "Start work"})
    assert result.status_code == 200, result.text
    return client.post(f"/api/tasks/{task_id}/runs", headers=headers(), json={"title": "Work starts", "idempotency_key": "context-start-001", **fields})


def context(client, task_id, actor="agent-a"):
    response = client.get(f"/api/tasks/{task_id}/context", headers=headers(actor))
    assert response.status_code == 200, response.text
    return response.json()


def work():
    return {"completed": [{"summary": "Added parser", "refs": ["artifact:parser-review"], "revision": "abcdef0123456789"}],
            "remaining": ["Review edge cases"], "next_action": "Review parser", "next_owner": "reviewer"}


def add_session(factory, task_id, **fields):
    with factory() as db:
        row = RuntimeSession(actor_id=fields.get("actor_id", "agent-a"), runtime=fields.get("runtime", "test-runtime"),
                             node=fields.get("node", "test-node"), task_id=task_id,
                             external_id="native-private-reference", content_hash="synthetic-hash", privacy="redacted",
                             title="Private conversation title", summary="Private summary text",
                             messages_json='[{"text":"Private conversation content"}]')
        db.add(row)
        db.flush()
        session_id = row.id
        db.commit()
    return session_id


def test_identity_is_opening_snapshot_and_reported_model_is_not_verified(environment):
    client, factory, task_id, _ = environment
    profile(factory)
    run = opening(client, task_id, model="test-model-v2").json()["run"]
    assert run["node"] == "test-node" and run["runtime"] == "test-runtime"
    assert run["model"] == "test-model-v2" and run["model_source"] == "reported"
    assert run["node_source"] == run["runtime_source"] == "registry"
    assert run["identity_complete"] is True
    with factory() as db:
        actor = db.get(Actor, "agent-a")
        actor.node, actor.runtime, actor.model = "other-node", "other-runtime", "other-model"
        db.commit()
    snapshot = context(client, task_id)["runs"][0]
    assert (snapshot["node"], snapshot["runtime"], snapshot["model"]) == ("test-node", "test-runtime", "test-model-v2")
    assert snapshot["identity_recorded_at"] == run["identity_recorded_at"]
    assert snapshot["reported_by"] == {"kind": "agent", "id": "agent-a"}


def test_registry_model_is_labelled_registry_and_blanks_remain_unknown(environment):
    client, factory, task_id, other_id = environment
    profile(factory)
    run = opening(client, task_id).json()["run"]
    assert run["model"] == "test-model-v1" and run["model_source"] == "registry"
    unknown = start(client, other_id)
    assert unknown["model"] is None and unknown["model_source"] == "unknown"
    assert unknown["node"] is None and unknown["identity_complete"] is False
    assert unknown["freshness"]["state"] == "unknown"


@pytest.mark.parametrize("placeholder", ["configured-at-runtime", "待确认", "未登记", "n/a", "auto", "default"])
def test_configured_at_runtime_is_unknown_model(environment, placeholder):
    client, factory, task_id, _ = environment
    profile(factory)
    with factory() as db:
        db.get(Actor, "agent-a").model = placeholder
        db.commit()
    run = opening(client, task_id).json()["run"]
    assert run["model"] is None and run["model_source"] == "unknown" and not run["identity_complete"]


def test_model_alias_does_not_claim_complete_model_identity(environment):
    client, factory, task_id, _ = environment
    profile(factory)
    run = opening(client, task_id, model="opus").json()["run"]
    assert run["model"] == "opus" and run["model_source"] == "reported"
    assert run["identity_complete"] is False


@pytest.mark.parametrize("scope", ["actor", "task", "node", "runtime", "missing"])
def test_session_binding_refuses_foreign_scope_without_event(environment, scope):
    client, factory, task_id, other_id = environment
    profile(factory)
    fields = {"actor_id": "agent-b"} if scope == "actor" else {}
    if scope in {"node", "runtime"}:
        fields[scope] = "unrelated"
    session_id = add_session(factory, other_id if scope == "task" else task_id, **fields)
    result = opening(client, task_id, runtime_session_id=999999 if scope == "missing" else session_id)
    assert result.status_code == 403, result.text
    assert context(client, task_id)["runs"] == []


def test_context_never_copies_private_session_and_limits_link_access(environment):
    client, factory, task_id, _ = environment
    profile(factory)
    session_id = add_session(factory, task_id)
    assert opening(client, task_id, runtime_session_id=session_id).status_code == 200
    own = context(client, task_id)
    foreign = context(client, task_id, "agent-b")
    assert own["runs"][0]["session_link"] == {"id": session_id, "access": "permitted"}
    assert foreign["runs"][0]["session_link"] is None
    for packet in (own, foreign):
        serialized = json.dumps(packet)
        for private in ("Private conversation", "Private summary", "native-private-reference", "messages_json", "runtime_session_id"):
            assert private not in serialized
        assert packet["privacy"]["contains_private_conversations"] is False
    login_operator(client)
    assert client.get(f"/api/tasks/{task_id}/context").json()["runs"][0]["session_link"]["id"] == session_id


@pytest.mark.parametrize(("registered", "synced"), [("openai-codex", "codex"), ("kimi-cli", "kimi")])
def test_session_binding_accepts_explicit_runtime_alias_without_rewriting_identity(environment, registered, synced):
    client, factory, task_id, _ = environment
    profile(factory)
    with factory() as db:
        db.get(Actor, "agent-a").runtime = registered
        db.commit()
    session_id = add_session(factory, task_id, runtime=synced)
    response = opening(client, task_id, runtime_session_id=session_id)
    assert response.status_code == 200, response.text
    assert response.json()["run"]["runtime"] == registered
    packet = context(client, task_id)
    assert packet["runs"][0]["session_link"]["id"] == session_id


def test_session_binding_does_not_treat_multi_as_any_runtime(environment):
    client, factory, task_id, _ = environment
    profile(factory)
    with factory() as db:
        db.get(Actor, "agent-a").runtime = "multi"
        db.commit()
    session_id = add_session(factory, task_id, runtime="codex")
    assert opening(client, task_id, runtime_session_id=session_id).status_code == 403


def test_progress_claims_stay_unverified_and_heartbeat_does_not_refresh_them(environment, monkeypatch):
    client, factory, task_id, _ = environment
    run = start(client, task_id, actor="agent-a")
    body = {"status": "running", "note": "Parser written", "progress_report": work(), "progress": {"completed": 1, "total": 2, "unit": "checks"}, "idempotency_key": "context-progress-01"}
    result = report(client, task_id, run["id"], actor="agent-a", **body)
    assert result.status_code == 200, result.text
    initial = context(client, task_id)
    assert initial["confirmed_progress"] == []
    assert initial["unverified_claims"][0]["verification"] == "unverified"
    assert initial["evidence"][0]["revision"] == "abcdef0123456789"
    assert initial["task"]["progress"] == 0
    assert initial["next"][0]["owner"] == "reviewer" and initial["next"][0]["grants_authority"] is False
    assert report(client, task_id, run["id"], actor="agent-a", **body).json()["created"] is False
    changed = {**body, "progress_report": {**work(), "remaining": ["Different work"]}}
    assert report(client, task_id, run["id"], actor="agent-a", **changed).status_code == 409
    past = result.json()["run"]["progress_reported_at"]
    later = dt.datetime.now(dt.timezone.utc) + dt.timedelta(hours=2)
    monkeypatch.setattr(collaboration, "utcnow", lambda: later)
    with factory() as db:
        db.get(Task, task_id).lease_heartbeat_at = later
        db.commit()
    assert report(client, task_id, run["id"], actor="agent-a", status="running", note="Still working", idempotency_key="context-note-001").status_code == 200
    packet = context(client, task_id)
    refreshed = packet["runs"][0]
    assert refreshed["progress_reported_at"] == past
    assert refreshed["heartbeat_at"] and refreshed["freshness"]["state"] == "stale"
    assert refreshed["last_reported_by"] == {"kind": "agent", "id": "agent-a"}
    assert packet["unverified_claims"][0]["freshness"]["state"] == "stale"


def test_bad_owner_and_credential_evidence_cannot_enter_progress_ledger(environment):
    client, _, task_id, _ = environment
    run = start(client, task_id, actor="agent-a")
    bad = work()
    bad["next_owner"] = "not-registered"
    assert report(client, task_id, run["id"], actor="agent-a", status="running", note="Update", progress_report=bad, idempotency_key="bad-owner-0001").status_code == 422
    bad = work()
    bad["completed"][0]["refs"] = ["sk-" + "x" * 40]
    assert report(client, task_id, run["id"], actor="agent-a", status="running", note="Update", progress_report=bad, idempotency_key="bad-evidence-01").status_code == 422
    assert context(client, task_id)["unverified_claims"] == []


def test_context_read_does_not_mutate_task_or_lease_and_legacy_task_is_explicit(environment):
    client, factory, task_id, _ = environment
    with factory() as db:
        before = (db.get(Task, task_id).updated_at, len(db.get(Task, task_id).events))
    response = context(client, task_id)
    assert response["runs"] == response["active_runs"] == response["confirmed_progress"] == response["unverified_claims"] == []
    assert response["collaboration"]["coverage"]["instrumented_tasks"] == 0
    assert len(response["revision"]["content_hash"]) == 64
    with factory() as db:
        assert before == (db.get(Task, task_id).updated_at, len(db.get(Task, task_id).events))


def test_expired_same_term_run_is_not_an_active_execution(environment):
    client, factory, task_id, _ = environment
    start(client, task_id, actor="agent-a")
    with factory() as db:
        task = db.get(Task, task_id)
        task.lease_term = 1
        task.lease_expires_at = dt.datetime.now(dt.timezone.utc) - dt.timedelta(seconds=1)
        event = db.scalar(select(TaskEvent).where(TaskEvent.task_id == task_id, TaskEvent.event_type == "run_started"))
        payload = json.loads(event.payload_json)
        payload["run"]["lease_term"] = 1
        event.payload_json = json.dumps(payload)
        db.commit()
    packet = context(client, task_id)
    assert packet["runs"][0]["lease_current"] is True
    assert packet["runs"][0]["lease_live"] is False
    assert packet["active_runs"] == []


def test_context_requires_auth_and_viewer_get_is_readonly(environment):
    client, _, task_id, _ = environment
    assert client.get(f"/api/tasks/{task_id}/context").status_code == 401
    assert client.post("/api/auth/login", json={"username": "observer", "password": "synthetic-password"}).status_code == 200
    assert client.get(f"/api/tasks/{task_id}/context").status_code == 200
    assert client.post(f"/api/tasks/{task_id}/runs", json={"title": "Forbidden", "idempotency_key": "viewer-start-001"}).status_code == 403


def test_recorded_done_is_explicit_state_evidence_not_independent_acceptance(environment):
    client, _, task_id, _ = environment
    start(client, task_id, actor="agent-a")
    response = client.post(f"/api/tasks/{task_id}/update", headers=headers(), json={"status": "done", "note": "Task finished"})
    assert response.status_code == 200, response.text
    packet = context(client, task_id)
    assert len(packet["confirmed_progress"]) == 1
    evidence = packet["confirmed_progress"][0]
    assert evidence["confirmation"] == "recorded_task_state" and evidence["acceptance_verified"] is False
    assert evidence["who"] == "agent-a" and evidence["event_id"] > 0


def test_old_run_without_identity_snapshot_never_uses_current_profile(environment):
    client, factory, task_id, _ = environment
    start(client, task_id, actor="agent-a")
    with factory() as db:
        event = db.scalar(select(TaskEvent).where(TaskEvent.task_id == task_id, TaskEvent.event_type == "run_started"))
        payload = json.loads(event.payload_json)
        for name in ("node", "runtime", "node_source", "runtime_source", "identity_complete", "identity_recorded_at", "execution_state", "prepared_at"):
            payload["run"].pop(name, None)
        event.payload_json = json.dumps(payload)
        db.commit()
    profile(factory)
    old = context(client, task_id)["runs"][0]
    assert old["node"] is None and old["runtime"] is None and old["model"] is None
    assert old["node_source"] == old["runtime_source"] == old["model_source"] == "unknown"
    assert old["identity_complete"] is False and old["execution_state"] == "started"


def test_mcp_context_orientation_and_claim_keep_briefings(monkeypatch):
    calls = []
    def capture(method, path, body=None):
        calls.append((method, path, body))
        if path.endswith("/claim"):
            return {"id": "task-20260101-001", "title": "Work", "status": "doing", "holder": "agent-a", "lease": {"term": 2}, "start_briefing": {"similar_tasks": []}, "skill_briefing": {"skills": []}}
        return {"ok": True}
    monkeypatch.setattr(mcp_bridge, "_call", capture)
    tools = mcp_bridge.create_server()._tool_manager._tools
    tools["orientation"].fn()
    assert calls[-1] == ("GET", "/api/orientation/context", None)
    tools["task_context"].fn("task-20260101-001")
    assert calls[-1][1] == "/api/tasks/task-20260101-001/context"
    claimed = tools["task_claim"].fn("task-20260101-001")
    assert claimed["start_briefing"] == {"similar_tasks": []} and claimed["skill_briefing"] == {"skills": []}
    assert claimed["lease"] == {"term": 2}
    tools["task_run_start"].fn("task-20260101-001", "Work", "mcp-context-001", runtime_session_id=8)
    assert calls[-1][2]["runtime_session_id"] == 8
    tools["task_run_report"].fn("task-20260101-001", "run-example", "running", "Progress", "mcp-progress-001", progress_report=work())
    assert calls[-1][2]["progress_report"] == work()


def test_execution_permission_is_consumed_once_across_stale_sessions_and_credentials(environment):
    client, factory, task_id, _ = environment
    with factory() as db:
        task = db.get(Task, task_id)
        task.lease_term = 1
        task.lease_expires_at = dt.datetime.now(dt.timezone.utc) + dt.timedelta(minutes=10)
        db.commit()
    run = opening(client, task_id, lease_term=1, execution_state="prepared").json()["run"]
    assert run["started_at"] is None and run["prepared_at"] is not None
    assert context(client, task_id)["active_runs"] == []
    premature = report(client, task_id, run["id"], actor="agent-a", status="running", note="Premature", execution_state="started", lease_term=1, idempotency_key="before-grant-001")
    assert premature.status_code == 409
    principal = Principal(kind="agent", name="agent-a", actor_id="agent-a", role="agent", credential_id="credential-first")
    second = Principal(kind="agent", name="agent-a", actor_id="agent-a", role="agent", credential_id="credential-second")
    with factory() as first_db, factory() as second_db:
        first_task = first_db.get(Task, task_id)
        second_task = second_db.get(Task, task_id)
        assert first_task.events and second_task.events
        allowed = collaboration.authorize_run_execution(first_db, task_id, run["id"], principal, 1, "execution-key-001")
        assert allowed["allow_execute"] is True
        first_db.commit()
        denied = collaboration.authorize_run_execution(second_db, task_id, run["id"], second, 1, "execution-key-002")
        assert denied["allow_execute"] is False
        second_db.commit()
    replay = client.post(f"/api/tasks/{task_id}/runs/{run['id']}/authorize-execution", headers=headers(), json={"lease_term": 1, "request_key": "execution-key-001"})
    assert replay.status_code == 200 and replay.json()["allow_execute"] is False
    with factory() as db:
        events = list(db.scalars(select(TaskEvent).where(TaskEvent.task_id == task_id, TaskEvent.event_type == "run_execution_authorized")))
        assert len(events) == 1
        assert "execution-key-001" not in events[0].payload_json
        assert json.loads(events[0].payload_json)["credential_id"] == "credential-first"
    snapshot = context(client, task_id)["runs"][0]
    assert snapshot["execution_authorized_at"] == allowed["execution_authorized_at"]
    assert snapshot["freshness"]["state"] == "unknown"
    assert snapshot["execution_state"] == "prepared" and snapshot["started_at"] is None
    begun = report(client, task_id, run["id"], actor="agent-a", status="running", note="Runtime started", execution_state="started", lease_term=1, idempotency_key="after-grant-001")
    assert begun.status_code == 200, begun.text
    assert begun.json()["run"]["execution_state"] == "started" and begun.json()["run"]["started_at"]
    assert len(context(client, task_id)["active_runs"]) == 1


def test_execution_permission_requires_current_holder_live_term(environment):
    client, factory, task_id, _ = environment
    with factory() as db:
        task = db.get(Task, task_id)
        task.lease_term = 1
        task.lease_expires_at = dt.datetime.now(dt.timezone.utc) + dt.timedelta(minutes=10)
        db.commit()
    run = opening(client, task_id, lease_term=1, execution_state="prepared").json()["run"]
    url = f"/api/tasks/{task_id}/runs/{run['id']}/authorize-execution"
    assert client.post(url, headers=headers("agent-b"), json={"lease_term": 1, "request_key": "execution-key-001"}).status_code == 403
    assert client.post(url, headers=headers(), json={"lease_term": 2, "request_key": "execution-key-001"}).status_code == 409
    with factory() as db:
        db.get(Task, task_id).lease_expires_at = dt.datetime.now(dt.timezone.utc) - dt.timedelta(seconds=1)
        db.commit()
    assert client.post(url, headers=headers(), json={"lease_term": 1, "request_key": "execution-key-001"}).status_code == 409
