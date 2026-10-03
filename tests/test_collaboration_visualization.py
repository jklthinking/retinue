"""Evidence-only graph, module attribution and legacy visual coverage."""
import json

from server.db import Task, TaskDependency
from server.deps import Principal
from server.collaboration import collaboration_projection, module_contributions
from server.engine import Forbidden
import pytest
from test_collaboration import environment, headers, delegation_body, start, report
from test_flow_api import env, make_flow, advance


def test_uninstrumented_task_keeps_recorded_history_and_dependency(environment):
    client, factory, task_id, prerequisite = environment
    with factory() as db:
        db.add(TaskDependency(dependent_id=task_id, prerequisite_id=prerequisite))
        old_events = len(db.get(Task, task_id).events)
        db.commit()
    view = client.get(f"/api/tasks/{task_id}/collaboration", headers=headers()).json()
    assert view["runs"] == [] and view["coverage"]["instrumented_tasks"] == 0
    assert view["tasks"][0]["depends_on"] == [prerequisite]
    assert view["events"][0]["to_status"] == "queued"
    assert view["events"][0]["to_holder"] == "agent-a"
    assert [task["id"] for task in view["related_tasks"]] == [prerequisite]
    assert view["relationships"] == [{"id": f"dependency:{prerequisite}:{task_id}",
        "kind": "dependency", "from_task_id": prerequisite, "to_task_id": task_id,
        "source": "task_dependency"}]
    assert view["modules"][0]["source"] == "unassigned"
    with factory() as db:
        assert len(db.get(Task, task_id).events) == old_events


def test_free_form_review_words_never_create_review_return(environment):
    client, _, task_id, _ = environment
    response = client.post(f"/api/tasks/{task_id}/update", headers=headers(),
        json={"note": "Review return and handoff are just words here"})
    assert response.status_code == 200
    view = client.get(f"/api/tasks/{task_id}/collaboration", headers=headers()).json()
    assert view["relationships"] == []


def test_explicit_modules_and_executor_evidence_have_separate_attribution(environment):
    client, _, root_id, _ = environment
    child = client.post(f"/api/tasks/{root_id}/delegations", headers=headers(),
                       json=delegation_body(module="Reader")).json()["delegation"]["child_task_id"]
    run = start(client, child)
    response = report(client, child, run["id"], status="running", note="Checks recorded",
        idempotency_key="module-report-0001", progress_report={"completed": [
            {"module": "Timeout contract", "summary": "Budget verified",
             "refs": ["commit:example-v1"], "revision": "example-v1"}], "remaining": ["Review"]})
    assert response.status_code == 200, response.text
    view = client.get(f"/api/tasks/{root_id}/collaboration", headers=headers()).json()
    assert next(task for task in view["tasks"] if task["id"] == child)["module"] == "Reader"
    module = next(item for item in view["modules"] if item["name"] == "Timeout contract")
    assert module["completed"][0]["actor_id"] == "agent-b"
    assert module["completed"][0]["verification"] == "unverified"
    assert module["completed"][0]["refs"] == ["commit:example-v1"]
    assert module["task_ids"] == [child]
    assert view["runs"][0]["module"] == "Reader"


def test_root_run_module_does_not_create_an_empty_unassigned_contribution(environment):
    client, factory, root_id, _ = environment
    response = client.post(f"/api/tasks/{root_id}/update", headers=headers(),
                           json={"status": "doing", "note": "Coordination starts"})
    assert response.status_code == 200
    response = client.post(f"/api/tasks/{root_id}/runs", headers=headers(), json={
        "title": "Coordinate delivery", "module": "Coordination",
        "idempotency_key": "root-module-run-01"})
    assert response.status_code == 200, response.text
    run_id = response.json()["run"]["id"]
    with factory() as db:
        original_events = len(db.get(Task, root_id).events)
    view = client.get(f"/api/tasks/{root_id}/collaboration", headers=headers()).json()
    assert view["tasks"][0]["module"] is None
    assert len(view["modules"]) == 1
    assert view["modules"][0]["name"] == "Coordination"
    assert view["modules"][0]["source"] == "explicit"
    assert view["modules"][0]["task_ids"] == [root_id]
    assert view["modules"][0]["run_ids"] == [run_id]
    with factory() as db:
        assert len(db.get(Task, root_id).events) == original_events


def test_unlabelled_task_keeps_distinct_run_and_completed_item_modules(environment):
    _, factory, root_id, _ = environment
    runs = [
        {"id": "run-parser", "task_id": root_id, "actor_id": "agent-a", "module": "Parser",
         "progress_reported_at": "2026-08-31T09:00:00Z", "progress_report": {"completed": [
             {"module": "Documentation", "summary": "Parser contract recorded",
              "refs": ["artifact:parser-contract"], "revision": "example-revision"}]}},
        {"id": "run-reader", "task_id": root_id, "actor_id": "agent-a", "module": "Reader"},
    ]
    with factory() as db:
        modules = {item["name"]: item for item in module_contributions([db.get(Task, root_id)], runs)}
    assert set(modules) == {"Parser", "Reader", "Documentation"}
    assert modules["Parser"]["run_ids"] == ["run-parser"]
    assert modules["Reader"]["run_ids"] == ["run-reader"]
    assert modules["Parser"]["completed"] == modules["Reader"]["completed"] == []
    evidence = modules["Documentation"]
    assert evidence["task_ids"] == [root_id] and evidence["run_ids"] == ["run-parser"]
    assert evidence["completed"] == [{"task_id": root_id, "run_id": "run-parser", "actor_id": "agent-a",
        "summary": "Parser contract recorded", "refs": ["artifact:parser-contract"],
        "revision": "example-revision", "at": "2026-08-31T09:00:00Z", "verification": "unverified"}]


def test_unknown_historical_run_and_uninstrumented_task_remain_unassigned(environment):
    _, factory, root_id, other_id = environment
    runs = [
        {"id": "run-known", "task_id": root_id, "actor_id": "agent-a", "module": "Coordination"},
        {"id": "run-legacy", "task_id": root_id, "actor_id": "agent-a"},
    ]
    with factory() as db:
        modules = {item["source"]: item for item in module_contributions(
            [db.get(Task, root_id), db.get(Task, other_id)], runs)}
    assert set(modules) == {"explicit", "unassigned"}
    assert modules["explicit"]["task_ids"] == [root_id]
    assert modules["explicit"]["run_ids"] == ["run-known"]
    assert set(modules["unassigned"]["task_ids"]) == {root_id, other_id}
    assert modules["unassigned"]["run_ids"] == ["run-legacy"]


def test_explicit_task_module_remains_when_its_run_contributes_elsewhere(environment):
    client, factory, root_id, _ = environment
    child = client.post(f"/api/tasks/{root_id}/delegations", headers=headers(),
                       json=delegation_body(module="Reader")).json()["delegation"]["child_task_id"]
    with factory() as db:
        modules = {item["name"]: item for item in module_contributions([db.get(Task, child)], [
            {"id": "run-review", "task_id": child, "actor_id": "agent-b", "module": "Review"}])}
    assert set(modules) == {"Reader", "Review"}
    assert modules["Reader"]["task_ids"] == [child] and modules["Reader"]["run_ids"] == []
    assert modules["Review"]["task_ids"] == [child] and modules["Review"]["run_ids"] == ["run-review"]


def test_absent_module_keeps_old_request_idempotence(environment):
    client, factory, task_id, _ = environment
    body = delegation_body()
    first = client.post(f"/api/tasks/{task_id}/delegations", headers=headers(), json=body)
    assert first.status_code == 200
    # Simulate an already-recorded request from the previous deployed schema.
    with factory() as db:
        event = next(item for item in db.get(Task, task_id).events if item.event_type == "delegation")
        payload = json.loads(event.payload_json)
        payload["request"].pop("module", None)
        event.payload_json = json.dumps(payload)
        db.commit()
    repeated = client.post(f"/api/tasks/{task_id}/delegations", headers=headers(), json=body)
    assert repeated.status_code == 200 and repeated.json()["created"] is False
    different = client.post(f"/api/tasks/{task_id}/delegations", headers=headers(), json={**body, "module": "Reader"})
    assert different.status_code == 409


def test_delegated_pipeline_requires_existing_scope_for_every_holder(environment):
    client, _, task_id, _ = environment
    stages = [{"name": "Inspect", "holder": "agent-b", "gate": "auto"},
              {"name": "Review", "holder": "agent-a", "gate": "review"}]
    response = client.post(f"/api/tasks/{task_id}/delegations", headers=headers(),
        json=delegation_body(key="pipeline-delegate-01", pipeline=stages, module="Reader"))
    assert response.status_code == 200, response.text
    child = response.json()["delegation"]["child_task_id"]
    assert client.get(f"/api/tasks/{child}", headers=headers()).json()["pipeline"] == stages
    forbidden = client.post(f"/api/tasks/{task_id}/delegations", headers=headers(),
        json=delegation_body(key="pipeline-delegate-02", pipeline=[stages[0],
            {"name": "Human gate", "holder": "reviewer", "gate": "queen"}]))
    assert forbidden.status_code == 403
    mismatch = client.post(f"/api/tasks/{task_id}/delegations", headers=headers(),
        json=delegation_body(key="pipeline-delegate-03", pipeline=list(reversed(stages))))
    assert mismatch.status_code == 422


def test_channel_cannot_expand_task_into_shared_collaboration(environment):
    _, factory, task_id, _ = environment
    with factory() as db:
        with pytest.raises(Forbidden):
            collaboration_projection(db, db.get(Task, task_id),
                Principal(kind="channel", name="entry", actor_id=None, role="channel"))


def test_typed_pipeline_return_is_distinct_from_handoff_and_chain_still_folds(env):
    client, _ = env
    task_id = make_flow(client)
    assert advance(client, task_id, "Draft submitted").status_code == 200
    assert client.post(f"/api/tasks/{task_id}/update", json={"status": "doing", "note": "Review begins"}).status_code == 200
    assert client.post(f"/api/tasks/{task_id}/stage-reject", json={"note": "Evidence is incomplete"}).status_code == 200
    view = client.get(f"/api/tasks/{task_id}/collaboration").json()
    kinds = [edge["kind"] for edge in view["relationships"]]
    assert kinds == ["handoff", "review_return"]
    assert view["relationships"][1]["from_actor"] == "checker"
    assert view["relationships"][1]["to_actor"] == "writer"
    annotations = [event for event in view["events"] if event["type"] == "pipeline_transition"]
    assert len(annotations) == 2
    assert annotations[1]["payload"]["pipeline_transition"]["kind"] == "review_return"
    audit = client.get(f"/api/tasks/{task_id}/drift").json()
    assert audit["in_sync"] is True
