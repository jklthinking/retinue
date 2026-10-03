"""End-to-end adapter launch safety using the real HTTP grant and ledger."""
import datetime as dt
import json

import pytest
from sqlalchemy import select

from server.db import Task, TaskEvent
from tools import retinue_run
from tools.retinue_worker import WorkerError
from test_collaboration import environment, headers


def adapter(environment, monkeypatch):
    client, factory, task_id, _ = environment
    with factory() as db:
        task = db.get(Task, task_id)
        task.lease_term = 1
        task.lease_expires_at = dt.datetime.now(dt.timezone.utc) + dt.timedelta(minutes=10)
        db.commit()
    response = client.post(f"/api/tasks/{task_id}/update", headers=headers(),
                           json={"status": "doing", "note": "Begin", "lease_term": 1})
    assert response.status_code == 200, response.text
    def call(base, token, method, path, body=None):
        response = client.request(method, path, headers=headers(), json=body)
        if response.status_code >= 400:
            raise WorkerError(f"HTTP {response.status_code}")
        return response.json()
    monkeypatch.setattr(retinue_run, "api_call", call)
    return client, factory, task_id


def test_adapter_launches_once_and_reports_structured_progress(environment, monkeypatch):
    client, factory, task_id = adapter(environment, monkeypatch)
    launched, stopped = [], []
    def launch(context):
        assert context["active_runs"] == []
        launched.append(context)
        return "synthetic-handle"
    run = retinue_run.start_controlled_run("unused", "synthetic", task_id, 1,
            title="Configured adapter", request_key="adapter-example-001", launch=launch,
            stop=stopped.append, model="example-model")
    assert len(launched) == 1 and stopped == []
    with pytest.raises(retinue_run.LaunchDenied):
        retinue_run.start_controlled_run("unused", "synthetic", task_id, 1,
            title="Configured adapter", request_key="adapter-example-001", launch=launch,
            stop=stopped.append, model="example-model")
    assert len(launched) == 1
    retinue_run.report_progress("unused", "synthetic", run, note="Implementation ready",
        completed=[{"summary": "Reader structure", "refs": ["commit:example-v1"], "revision": "example-v1"}],
        remaining=["Independent review"], next_action="Review reader", next_owner="agent-b")
    packet = client.get(f"/api/tasks/{task_id}/context", headers=headers()).json()
    assert packet["active_runs"][0]["execution_state"] == "started"
    assert packet["unverified_claims"][0]["completed"][0]["summary"] == "Reader structure"
    assert packet["next"][0]["owner"] == "agent-b"
    with factory() as db:
        event = db.scalar(select(TaskEvent).where(TaskEvent.task_id == task_id,
            TaskEvent.event_type == "run_execution_authorized"))
        assert json.loads(event.payload_json)["credential_id"].startswith("agent-token:")


def test_adapter_stops_exact_handle_if_start_receipt_is_fenced(environment, monkeypatch):
    client, factory, task_id = adapter(environment, monkeypatch)
    stopped = []
    def launch(context):
        with factory() as db:
            db.get(Task, task_id).lease_expires_at = dt.datetime.now(dt.timezone.utc) - dt.timedelta(seconds=1)
            db.commit()
        return "exact-handle"
    with pytest.raises(WorkerError):
        retinue_run.start_controlled_run("unused", "synthetic", task_id, 1,
            title="Configured adapter", launch=launch, stop=stopped.append)
    assert stopped == ["exact-handle"]
    packet = client.get(f"/api/tasks/{task_id}/context", headers=headers()).json()
    assert packet["active_runs"] == []
    assert packet["runs"][0]["execution_state"] == "prepared"


def test_transport_uncertainty_never_launches_or_retries(monkeypatch):
    calls, launched = [], []
    def call(base, token, method, path, body=None):
        calls.append(path)
        if path.endswith("/context"):
            return {"version": 1, "task_id": "task-example"}
        if path.endswith("/runs"):
            return {"run": {"id": "run-example"}}
        raise WorkerError("transport outcome unknown")
    monkeypatch.setattr(retinue_run, "api_call", call)
    with pytest.raises(WorkerError):
        retinue_run.start_controlled_run("unused", "synthetic", "task-example", 1,
            title="Configured adapter", launch=launched.append, stop=lambda handle: None)
    assert len(calls) == 3 and launched == []
