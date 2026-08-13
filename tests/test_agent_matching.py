from __future__ import annotations

import datetime as dt
import json

from fastapi.testclient import TestClient

from server.app import create_app
from server.db import (
    Actor,
    Node,
    NodeRuntime,
    Skill,
    User,
    make_session_factory,
    utcnow,
)
from server.security import hash_password


def _client(tmp_path) -> TestClient:
    factory = make_session_factory(tmp_path / "matching.db")
    now = utcnow().replace(tzinfo=None)
    with factory() as db:
        db.add_all(
            [
                Actor(id="teacher", kind="human", display_name="Teacher"),
                Actor(
                    id="lesson-planner",
                    kind="agent",
                    display_name="Lesson Planner",
                    runtime="claude-code",
                    last_seen_at=now - dt.timedelta(minutes=30),
                ),
                Actor(
                    id="material-maker",
                    kind="agent",
                    display_name="Material Maker",
                    runtime="codex",
                    last_seen_at=now,
                ),
                Actor(
                    id="retired-agent",
                    kind="agent",
                    display_name="Retired",
                    disabled=True,
                    last_seen_at=now,
                ),
                Skill(
                    name="备课包生成",
                    category="备课",
                    description="把课程要求整理为完整备课包",
                    owners_json=json.dumps(["lesson-planner"]),
                ),
                Skill(
                    name="课件与练习",
                    category="备课",
                    description="制作互动练习、课堂课件和课后作业",
                    owners_json=json.dumps(["material-maker"]),
                ),
                User(
                    username="teacher",
                    password_hash=hash_password("teacher-pass-123"),
                    role="member",
                    actor_id="teacher",
                ),
            ]
        )
        db.commit()
    client = TestClient(create_app(factory))
    assert (
        client.post(
            "/api/auth/login",
            json={"username": "teacher", "password": "teacher-pass-123"},
        ).status_code
        == 200
    )
    return client


def test_agent_match_is_explainable_and_skill_first(tmp_path):
    client = _client(tmp_path)

    response = client.get("/api/agent-match?q=请制作互动练习和课后作业")

    assert response.status_code == 200
    rows = response.json()
    assert [row["id"] for row in rows] == ["material-maker", "lesson-planner"]
    assert rows[0]["score"] > rows[1]["score"]
    assert rows[0]["matched_skills"] == ["课件与练习"]
    assert any("能力匹配" in reason for reason in rows[0]["reasons"])
    assert any("在线" in reason for reason in rows[0]["reasons"])
    assert "retired-agent" not in {row["id"] for row in rows}


def test_agent_match_supports_chinese_partial_intent_and_limit(tmp_path):
    client = _client(tmp_path)

    response = client.get("/api/agent-match?q=七年级英语完整备课包&limit=1")

    assert response.status_code == 200
    rows = response.json()
    assert len(rows) == 1
    assert rows[0]["id"] == "lesson-planner"


def test_confirmed_runtime_outranks_an_otherwise_identical_unconfirmed_claim(tmp_path):
    client = _client(tmp_path)
    now = utcnow().replace(tzinfo=None)
    factory = client.app.state.session_factory
    with factory() as db:
        db.add_all(
            [
                Actor(
                    id="confirmed-writer",
                    kind="agent",
                    display_name="Writer",
                    role="Report writer",
                    goal="Produce clear weekly reports.",
                    runtime="codex",
                    node="confirmed-node",
                    last_seen_at=now,
                ),
                Actor(
                    id="unconfirmed-writer",
                    kind="agent",
                    display_name="Writer",
                    role="Report writer",
                    goal="Produce clear weekly reports.",
                    runtime="codex",
                    node="unconfirmed-node",
                    last_seen_at=now,
                ),
                Node(id="confirmed-node", runtimes_probed_at=now),
                Node(id="unconfirmed-node", runtimes_probed_at=now),
                NodeRuntime(
                    node_id="confirmed-node",
                    runtime="codex",
                    command="codex",
                    available=True,
                    source="path",
                    detected_at=now,
                ),
            ]
        )
        db.commit()

    rows = {
        row["id"]: row
        for row in client.get("/api/agent-match?q=Produce weekly reports").json()
    }

    assert rows["confirmed-writer"]["score"] > rows["unconfirmed-writer"]["score"]
    assert any("新鲜探针确认" in reason for reason in rows["confirmed-writer"]["reasons"])
    assert any("最新清单未确认" in reason for reason in rows["unconfirmed-writer"]["reasons"])


def test_stale_probe_is_not_runtime_confirmation(tmp_path):
    client = _client(tmp_path)
    now = utcnow().replace(tzinfo=None)
    factory = client.app.state.session_factory
    with factory() as db:
        db.add_all(
            [
                Actor(
                    id="fresh-planner",
                    kind="agent",
                    display_name="Planner",
                    role="Release planner",
                    goal="Prepare release plans.",
                    runtime="codex",
                    node="fresh-node",
                    last_seen_at=now,
                ),
                Actor(
                    id="stale-planner",
                    kind="agent",
                    display_name="Planner",
                    role="Release planner",
                    goal="Prepare release plans.",
                    runtime="codex",
                    node="stale-node",
                    last_seen_at=now,
                ),
                Node(id="fresh-node", runtimes_probed_at=now),
                Node(
                    id="stale-node",
                    runtimes_probed_at=now - dt.timedelta(hours=25),
                ),
                NodeRuntime(
                    node_id="fresh-node",
                    runtime="codex",
                    command="codex",
                    available=True,
                    source="path",
                    detected_at=now,
                ),
                NodeRuntime(
                    node_id="stale-node",
                    runtime="codex",
                    command="codex",
                    available=True,
                    source="path",
                    detected_at=now - dt.timedelta(hours=25),
                ),
            ]
        )
        db.commit()

    rows = {
        row["id"]: row
        for row in client.get("/api/agent-match?q=Prepare release plans").json()
    }

    assert rows["fresh-planner"]["score"] > rows["stale-planner"]["score"]
    assert any(
        "探针已过期" in reason and "不算确认" in reason
        for reason in rows["stale-planner"]["reasons"]
    )


def test_reasons_explain_purpose_presence_absence_activity_and_runtime_weakness(tmp_path):
    client = _client(tmp_path)
    now = utcnow().replace(tzinfo=None)
    factory = client.app.state.session_factory
    with factory() as db:
        db.add(
            Actor(
                id="quiet-writer",
                kind="agent",
                display_name="Quiet Writer",
                role="Writer",
                goal="Draft concise reports.",
                runtime="codex",
                node="empty-node",
                last_seen_at=now - dt.timedelta(hours=2),
            )
        )
        db.add(Node(id="empty-node", runtimes_probed_at=now))
        db.commit()

    rows = {
        row["id"]: row
        for row in client.get("/api/agent-match?q=数据库调优").json()
    }
    reasons = rows["quiet-writer"]["reasons"]
    legacy_reasons = rows["lesson-planner"]["reasons"]

    assert any("职责(role)未匹配" in reason for reason in reasons)
    assert any("目标(goal)未匹配" in reason for reason in reasons)
    assert any("最近 15 分钟未见" in reason for reason in reasons)
    assert any("最新清单未确认" in reason for reason in reasons)
    assert any("未声明职责(role)与目标(goal)" in reason for reason in legacy_reasons)


def test_undeclared_purpose_remains_matchable_and_recommendation_is_read_only(tmp_path):
    client = _client(tmp_path)
    created = client.post(
        "/api/tasks",
        json={
            "title": "Prepare a lesson",
            "holder": "lesson-planner",
            "note": "Synthetic task created before matching",
        },
    )
    assert created.status_code == 200
    before = client.get(f"/api/tasks/{created.json()['id']}").json()

    response = client.get("/api/agent-match?q=七年级英语完整备课包")

    assert response.status_code == 200
    rows = response.json()
    assert rows[0]["id"] == "lesson-planner"
    assert rows[0]["role"] == rows[0]["goal"] == ""
    assert rows[0]["matched_skills"] == ["备课包生成"]
    after = client.get(f"/api/tasks/{created.json()['id']}").json()
    assert (after["status"], after["holder"], after["chain"]) == (
        before["status"],
        before["holder"],
        before["chain"],
    )
