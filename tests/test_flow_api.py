"""Pipeline flows and queen-gate approvals through the HTTP API."""

from __future__ import annotations

import pytest
from fastapi.routing import iter_route_contexts
from fastapi.testclient import TestClient

from server.app import create_app
from server.db import Actor, Approval, User, make_session_factory
from server.security import hash_password, hash_token
from server.seed import seed_demo


@pytest.fixture()
def env(tmp_path):
    factory = make_session_factory(tmp_path / "flow.db")
    with factory() as db:
        for actor_id, kind in [("boss", "human"), ("writer", "agent"), ("checker", "agent")]:
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


PIPELINE = [
    {"name": "撰写", "holder": "writer", "gate": "auto"},
    {"name": "审阅", "holder": "checker", "gate": "review"},
    {"name": "女王门", "holder": "boss", "gate": "queen"},
]


def make_flow(client) -> str:
    created = client.post(
        "/api/tasks", json={"title": "讲义流水线", "pipeline": PIPELINE}
    )
    assert created.status_code == 200
    body = created.json()
    assert body["holder"] == "writer" and body["pipeline_stage"] == 0
    return body["id"]


def advance(client, task_id: str, note: str):
    client.post(f"/api/tasks/{task_id}/update", json={"status": "doing", "note": "接棒开工"})
    return client.post(f"/api/tasks/{task_id}/stage-done", json={"note": note})


def test_full_pipeline_with_queen_gate(env):
    client, factory = env
    task_id = make_flow(client)

    first = advance(client, task_id, "初稿完成")
    assert first.status_code == 200
    assert first.json()["holder"] == "checker"
    assert first.json()["status"] == "handoff"
    assert first.json()["pipeline_stage"] == 1

    second = advance(client, task_id, "审阅通过")
    assert second.json()["pipeline_stage"] == 2
    assert second.json()["holder"] == "boss"

    pending = client.get("/api/approvals?pending=true").json()
    assert len(pending) == 1 and pending[0]["stage_name"] == "女王门"

    decided = client.post(
        f"/api/approvals/{pending[0]['id']}/decide",
        json={"decision": "approve", "note": "很好"},
    )
    assert decided.status_code == 200
    assert decided.json()["status"] == "done"
    assert decided.json()["progress"] == 100

    again = client.post(
        f"/api/approvals/{pending[0]['id']}/decide", json={"decision": "approve"}
    )
    assert again.status_code == 422  # already settled


def test_reject_paths(env):
    client, factory = env
    task_id = make_flow(client)
    advance(client, task_id, "初稿完成")

    client.post(f"/api/tasks/{task_id}/update", json={"status": "doing", "note": "开审"})
    rejected = client.post(
        f"/api/tasks/{task_id}/stage-reject", json={"note": "案例不足,退回补写"}
    )
    assert rejected.status_code == 200
    assert rejected.json()["pipeline_stage"] == 0
    assert rejected.json()["holder"] == "writer"

    advance(client, task_id, "补写完成")
    advance(client, task_id, "审阅通过")
    pending = client.get("/api/approvals?pending=true").json()
    queen_reject = client.post(
        f"/api/approvals/{pending[0]['id']}/decide",
        json={"decision": "reject", "note": "格式重排"},
    )
    assert queen_reject.status_code == 200
    assert queen_reject.json()["pipeline_stage"] == 1
    assert queen_reject.json()["holder"] == "checker"


def test_stage_done_requires_doing_and_holder(env):
    client, factory = env
    task_id = make_flow(client)
    not_doing = client.post(f"/api/tasks/{task_id}/stage-done", json={"note": "偷跑"})
    assert not_doing.status_code == 422

    bad_pipeline = client.post(
        "/api/tasks",
        json={"title": "x", "pipeline": [{"name": "只有一节", "holder": "writer"}]},
    )
    assert bad_pipeline.status_code == 422

    queen_first = client.post(
        "/api/tasks",
        json={
            "title": "y",
            "pipeline": [
                {"name": "门", "holder": "boss", "gate": "queen"},
                {"name": "写", "holder": "writer", "gate": "auto"},
            ],
        },
    )
    assert queen_first.status_code == 422


def test_feishu_act_link_requires_explicit_post_and_binds_decision(env):
    client, factory = env
    task_id = make_flow(client)
    advance(client, task_id, "初稿完成")
    advance(client, task_id, "审阅通过")

    approve_token = "rga_test-approve"
    reject_token = "rgr_test-reject"
    with factory() as db:
        approval = db.query(Approval).filter_by(status="pending").one()
        approval_id = approval.id
        approval.token_hash = hash_token(approve_token)
        approval.reject_token_hash = hash_token(reject_token)
        db.commit()

    bad = client.get(f"/api/approvals/{approval_id}/act?token=rga_forged&decision=approve")
    assert "链接无效" in bad.text
    wrong_decision = client.get(
        f"/api/approvals/{approval_id}/act?token={approve_token}&decision=reject"
    )
    assert "链接无效" in wrong_decision.text

    link = f"/api/approvals/{approval_id}/act?token={approve_token}&decision=approve"
    confirm = client.get(link)
    assert confirm.status_code == 200
    assert "确认批准" in confirm.text
    assert "<form method='post'>" in confirm.text
    assert client.get(f"/api/tasks/{task_id}").json()["status"] == "handoff"
    assert client.get("/api/approvals?pending=true").json()[0]["id"] == approval_id

    decided = client.post(link)
    assert decided.status_code == 200
    assert "已批准" in decided.text
    assert client.get(f"/api/tasks/{task_id}").json()["status"] == "done"
    replay = client.post(link)
    assert "已经裁决过了" in replay.text


def test_pipeline_templates_crud(env):
    client, factory = env
    saved = client.post(
        "/api/pipeline-templates", json={"name": "两审流程", "stages": PIPELINE}
    )
    assert saved.status_code == 200
    listed = client.get("/api/pipeline-templates").json()
    assert listed and listed[0]["name"] == "两审流程"
    assert len(listed[0]["stages"]) == 3


def test_queen_gate_requires_human_holder(env):
    client, _factory = env
    response = client.post(
        "/api/tasks",
        json={
            "title": "agent cannot approve itself",
            "pipeline": [
                {"name": "写作", "holder": "writer", "gate": "auto"},
                {"name": "伪女王门", "holder": "checker", "gate": "queen"},
            ],
        },
    )
    assert response.status_code == 422
    assert "human actor" in response.json()["detail"]


def test_generic_update_cannot_bypass_pipeline_or_queen_gate(env):
    client, _factory = env
    task_id = make_flow(client)

    reassign = client.post(
        f"/api/tasks/{task_id}/update",
        json={"holder": "checker", "note": "off-flow reassign"},
    )
    assert reassign.status_code == 422

    advance(client, task_id, "初稿完成")
    advance(client, task_id, "审阅通过")
    for body in (
        {"status": "doing", "note": "skip approval"},
        {"status": "cancelled", "note": "orphan approval"},
    ):
        response = client.post(f"/api/tasks/{task_id}/update", json=body)
        assert response.status_code == 422

    task = client.get(f"/api/tasks/{task_id}").json()
    assert task["status"] == "handoff" and task["holder"] == "boss"
    assert len(client.get("/api/approvals?pending=true").json()) == 1


def test_progress_resets_on_handoff_and_review_reject(env):
    client, _factory = env
    task_id = make_flow(client)
    client.post(f"/api/tasks/{task_id}/update", json={"status": "doing", "note": "开工"})
    client.post(
        f"/api/tasks/{task_id}/update",
        json={"progress": 80, "note": "进度 80"},
    )
    handed = client.post(
        f"/api/tasks/{task_id}/stage-done", json={"note": "初稿完成"}
    )
    assert handed.status_code == 200
    assert handed.json()["progress"] == 0

    client.post(f"/api/tasks/{task_id}/update", json={"status": "doing", "note": "开审"})
    client.post(
        f"/api/tasks/{task_id}/update",
        json={"progress": 60, "note": "审阅 60"},
    )
    rejected = client.post(
        f"/api/tasks/{task_id}/stage-reject", json={"note": "补充案例"}
    )
    assert rejected.status_code == 200
    assert rejected.json()["pipeline_stage"] == 0
    assert rejected.json()["progress"] == 0


def test_rejecting_to_queen_gate_opens_fresh_approval(env):
    client, _factory = env
    pipeline = [
        {"name": "撰写", "holder": "writer", "gate": "auto"},
        {"name": "女王门", "holder": "boss", "gate": "queen"},
        {"name": "复核", "holder": "checker", "gate": "review"},
    ]
    created = client.post("/api/tasks", json={"title": "退回女王门", "pipeline": pipeline})
    task_id = created.json()["id"]
    advance(client, task_id, "初稿完成")
    first = client.get("/api/approvals?pending=true").json()[0]
    approved = client.post(
        f"/api/approvals/{first['id']}/decide", json={"decision": "approve"}
    )
    assert approved.status_code == 200 and approved.json()["pipeline_stage"] == 2

    client.post(f"/api/tasks/{task_id}/update", json={"status": "doing", "note": "复核"})
    rejected = client.post(
        f"/api/tasks/{task_id}/stage-reject", json={"note": "请重新确认"}
    )
    assert rejected.status_code == 200
    assert rejected.json()["pipeline_stage"] == 1
    assert rejected.json()["holder"] == "boss"
    pending = client.get("/api/approvals?pending=true").json()
    assert len(pending) == 1 and pending[0]["id"] != first["id"]


def test_failed_approval_movement_rolls_back_settlement(env):
    client, factory = env
    pipeline = [
        {"name": "撰写", "holder": "writer", "gate": "auto"},
        {"name": "女王门", "holder": "boss", "gate": "queen"},
        {"name": "发布", "holder": "checker", "gate": "auto"},
    ]
    created = client.post("/api/tasks", json={"title": "原子裁决", "pipeline": pipeline})
    task_id = created.json()["id"]
    advance(client, task_id, "送审")
    pending = client.get("/api/approvals?pending=true").json()[0]

    with factory() as db:
        db.get(Actor, "checker").disabled = True
        db.commit()
    failed = client.post(
        f"/api/approvals/{pending['id']}/decide", json={"decision": "approve"}
    )
    assert failed.status_code == 422

    task = client.get(f"/api/tasks/{task_id}").json()
    assert task["pipeline_stage"] == 1
    assert task["status"] == "handoff" and task["holder"] == "boss"
    with factory() as db:
        approval = db.get(Approval, pending["id"])
        assert approval.status == "pending" and approval.decided_by is None


def test_review_reject_requires_doing_and_preserves_stage(env):
    client, _factory = env
    task_id = make_flow(client)
    advance(client, task_id, "初稿完成")
    client.post(f"/api/tasks/{task_id}/update", json={"status": "doing", "note": "开审"})
    client.post(
        f"/api/tasks/{task_id}/update",
        json={"status": "blocked", "blocked_reason": "等待材料", "note": "受阻"},
    )
    response = client.post(
        f"/api/tasks/{task_id}/stage-reject", json={"note": "不能从 blocked 打回"}
    )
    assert response.status_code == 422
    task = client.get(f"/api/tasks/{task_id}").json()
    assert task["pipeline_stage"] == 1 and task["status"] == "blocked"


def test_confirmation_page_escapes_untrusted_task_title(env):
    client, factory = env
    created = client.post(
        "/api/tasks", json={"title": "<script>alert(1)</script>", "pipeline": PIPELINE}
    )
    task_id = created.json()["id"]
    advance(client, task_id, "初稿完成")
    advance(client, task_id, "审阅通过")
    token = "rga_xss-test"
    with factory() as db:
        approval = db.query(Approval).filter_by(status="pending").one()
        approval.token_hash = hash_token(token)
        approval_id = approval.id
        db.commit()

    page = client.get(
        f"/api/approvals/{approval_id}/act?token={token}&decision=approve"
    )
    assert "<script>alert(1)</script>" not in page.text
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in page.text
    assert page.headers["referrer-policy"] == "no-referrer"


def test_database_transactions_finish_before_response(env):
    client, _factory = env
    scopes = []

    def visit(dependant):
        for child in dependant.dependencies:
            if getattr(child.call, "__name__", "") == "get_db":
                scopes.append(child.scope)
            visit(child)

    # iter_route_contexts flattens routers deferred by include_router, so the
    # walk sees every route's dependant exactly as the app serves it.
    for context in iter_route_contexts(client.app.routes):
        dependant = getattr(context.route, "dependant", None)
        if dependant is not None:
            visit(dependant)

    assert scopes
    assert set(scopes) == {"function"}


def test_demo_seed_syncs_existing_approval_role(tmp_path):
    factory = make_session_factory(tmp_path / "seed.db")
    with factory() as db:
        seed_demo(db, "edu")
        db.commit()
    with factory() as db:
        user = db.query(User).filter_by(username="dean").one()
        user.role = "member"
        db.commit()
    with factory() as db:
        seed_demo(db, "edu")
        user = db.query(User).filter_by(username="dean").one()
        assert user.role == "admin"
