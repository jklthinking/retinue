"""Card-pipeline templates, server guardrails, and checkpoint resume."""

from __future__ import annotations

import json

from fastapi.testclient import TestClient

from server.app import create_app
from server.db import Actor, User, make_session_factory
from server.pipeline_v2 import instantiate_card_pipeline
from server.security import hash_password


def _env(tmp_path):
    factory = make_session_factory(tmp_path / "pipeline-v2.db")
    with factory() as db:
        for actor_id, kind in [
            ("boss", "human"),
            ("writer", "agent"),
            ("checker", "agent"),
        ]:
            db.add(Actor(id=actor_id, kind=kind, display_name=actor_id))
        db.add(
            User(
                username="boss",
                password_hash=hash_password("boss-pass-123"),
                role="admin",
                actor_id="boss",
            )
        )
        db.commit()
    client = TestClient(create_app(factory))
    client.post("/api/auth/login", json={"username": "boss", "password": "boss-pass-123"})
    return client, factory


CHAIN = {
    "name": "brief-train",
    "nodes": [
        {
            "key": "research",
            "title": "Collect sources",
            "holder": "writer",
            "acceptance": [
                "list three sources",
                {
                    "check": "required_fields",
                    "fields": ["sources"],
                },
            ],
        },
        {
            "key": "draft",
            "title": "Write the brief",
            "holder": "checker",
            "depends_on": ["research"],
            "acceptance": [
                {"check": "tests_green"},
            ],
        },
    ],
}


def test_instantiate_wires_dependencies(tmp_path):
    client, _factory = _env(tmp_path)
    created = client.post("/api/card-pipelines", json=CHAIN)
    assert created.status_code == 200, created.text
    template_id = created.json()["id"]
    listed = client.get("/api/card-pipelines").json()
    assert listed[0]["name"] == "brief-train"
    assert listed[0]["spec"]["order"] == ["research", "draft"]

    inst = client.post(
        f"/api/card-pipelines/{template_id}/instantiate",
        json={"instance_key": "wave-one"},
    )
    assert inst.status_code == 200, inst.text
    body = inst.json()
    assert body["status"] == "running"
    assert body["progress"] == {"done": 0, "total": 2}
    assert body["cursor"] == "research"
    by_key = {node["key"]: node for node in body["nodes"]}
    research_id = by_key["research"]["task_id"]
    draft_id = by_key["draft"]["task_id"]
    assert research_id and draft_id

    research = client.get(f"/api/tasks/{research_id}").json()
    draft = client.get(f"/api/tasks/{draft_id}").json()
    assert research["holder"] == "writer"
    assert draft["holder"] == "checker"
    assert draft["depends_on"] == [research_id]
    assert draft["ready"] is False
    assert any(item.startswith("{") for item in research["acceptance"])

    again = client.post(
        f"/api/card-pipelines/{template_id}/instantiate",
        json={"instance_key": "wave-one"},
    )
    assert again.json()["id"] == body["id"]
    assert {node["task_id"] for node in again.json()["nodes"]} == {
        research_id,
        draft_id,
    }


def test_guardrail_rejects_done_without_evidence(tmp_path):
    client, _factory = _env(tmp_path)
    created = client.post(
        "/api/tasks",
        json={
            "title": "Need a sources field",
            "holder": "writer",
            "acceptance": [
                json.dumps(
                    {"check": "required_fields", "fields": ["sources"]},
                    sort_keys=True,
                ),
                json.dumps({"check": "tests_green"}, sort_keys=True),
            ],
        },
    )
    assert created.status_code == 200, created.text
    task_id = created.json()["id"]
    started = client.post(
        f"/api/tasks/{task_id}/update",
        json={"status": "doing", "note": "start"},
    )
    assert started.status_code == 200, started.text

    rejected = client.post(
        f"/api/tasks/{task_id}/update",
        json={"status": "done", "note": "finished without evidence"},
    )
    assert rejected.status_code == 422, rejected.text
    detail = rejected.json()["detail"]
    assert "guardrail rejected done" in detail
    assert "sources" in detail
    assert "tests" in detail
    card = client.get(f"/api/tasks/{task_id}").json()
    assert card["status"] == "doing"
    assert card["progress"] != 100

    still_bad = client.post(
        f"/api/tasks/{task_id}/update",
        json={
            "status": "done",
            "note": "fields only",
            "evidence": {"fields": {"sources": "three papers"}},
        },
    )
    assert still_bad.status_code == 422
    assert "tests" in still_bad.json()["detail"]

    passed = client.post(
        f"/api/tasks/{task_id}/update",
        json={
            "status": "done",
            "note": "evidence complete",
            "evidence": {
                "fields": {"sources": "three papers"},
                "tests": {"passed": 4, "failed": 0},
            },
        },
    )
    assert passed.status_code == 200, passed.text
    done = passed.json()
    assert done["status"] == "done"
    assert done["progress"] == 100
    events = client.get(f"/api/tasks/{task_id}").json()["chain"]
    assert any(event.get("type") == "guardrail" for event in events)


def test_checkpoint_resume_from_interrupted_instantiate(tmp_path):
    client, factory = _env(tmp_path)
    created = client.post("/api/card-pipelines", json=CHAIN)
    template_id = created.json()["id"]

    with factory() as db:
        from server.db import CardPipelineTemplate

        template = db.get(CardPipelineTemplate, template_id)
        instance = instantiate_card_pipeline(
            db,
            template,
            created_by="boss",
            instance_key="paused-run",
            stop_after="research",
        )
        db.commit()
        instance_id = instance.id

    paused = client.get(f"/api/card-pipeline-instances/{instance_id}").json()
    assert paused["status"] == "interrupted"
    assert paused["cursor"] == "draft"
    assert paused["progress"]["done"] == 0
    assert paused["progress"]["total"] == 2
    by_key = {node["key"]: node for node in paused["nodes"]}
    assert by_key["research"]["task_id"]
    assert by_key["draft"]["task_id"] is None

    resumed = client.post(
        f"/api/card-pipeline-instances/{instance_id}/resume"
    )
    assert resumed.status_code == 200, resumed.text
    body = resumed.json()
    assert body["status"] == "running"
    by_key = {node["key"]: node for node in body["nodes"]}
    assert by_key["draft"]["task_id"]
    draft = client.get(f"/api/tasks/{by_key['draft']['task_id']}").json()
    assert draft["depends_on"] == [by_key["research"]["task_id"]]

    # Completing the first card advances the visible cursor.
    research_id = by_key["research"]["task_id"]
    client.post(
        f"/api/tasks/{research_id}/update",
        json={"status": "doing", "note": "start research"},
    )
    finish = client.post(
        f"/api/tasks/{research_id}/update",
        json={
            "status": "done",
            "note": "sources ready",
            "evidence": {
                "fields": {"sources": "alpha, beta, gamma"},
                "tests": {"passed": 1, "failed": 0},
            },
        },
    )
    assert finish.status_code == 200, finish.text
    progressed = client.get(f"/api/card-pipeline-instances/{instance_id}").json()
    assert progressed["cursor"] == "draft"
    assert progressed["progress"] == {"done": 1, "total": 2}
    assert progressed["status"] == "running"


def test_reclaim_sweep_resumes_interrupted_instance(tmp_path):
    client, factory = _env(tmp_path)
    created = client.post("/api/card-pipelines", json=CHAIN)
    template_id = created.json()["id"]
    with factory() as db:
        from server.db import CardPipelineTemplate

        template = db.get(CardPipelineTemplate, template_id)
        instance = instantiate_card_pipeline(
            db,
            template,
            created_by="boss",
            instance_key="sweep-run",
            stop_after="research",
        )
        db.commit()
        instance_id = instance.id

    swept = client.post("/api/tasks/reclaim")
    assert swept.status_code == 200, swept.text
    resumed = swept.json()["resumed"]
    assert any(item["instance_id"] == instance_id for item in resumed)
    status = client.get(f"/api/card-pipeline-instances/{instance_id}").json()
    assert status["status"] == "running"
    assert all(node["task_id"] for node in status["nodes"])
