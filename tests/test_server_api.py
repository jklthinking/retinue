"""API-level tests: auth, board flow, holder-only-writes, metrics."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import event, update

import server.app as app_module
from server.app import create_app
from server.db import Actor, Node, NodeRuntime, NodeToken, Task, User, make_session_factory
from server.engine import create_task, list_task_summaries, task_summary_to_dict
from server.security import hash_password, hash_token, new_token


@pytest.fixture()
def client(tmp_path):
    factory = make_session_factory(tmp_path / "test.db")
    with factory() as db:
        db.add(Actor(id="owner", kind="human", display_name="负责人"))
        db.add(Actor(id="reviewer", kind="human", display_name="复核人"))
        db.add(Actor(id="scribe", kind="agent", display_name="撰稿"))
        db.add(
            User(
                username="owner",
                password_hash=hash_password("owner-pass-123"),
                role="admin",
                actor_id="owner",
            )
        )
        db.add(
            User(
                username="reviewer",
                password_hash=hash_password("owner-pass-123"),
                role="admin",
                actor_id="reviewer",
            )
        )
        db.commit()
    (tmp_path / "site-config.json").write_text(
        '{"demo_user": "owner"}', encoding="utf-8"
    )
    app = create_app(factory, data_dir=tmp_path)
    return TestClient(app)


def login(client: TestClient) -> None:
    response = client.post(
        "/api/auth/login", json={"username": "owner", "password": "owner-pass-123"}
    )
    assert response.status_code == 200


def agent_headers(client: TestClient) -> dict[str, str]:
    login(client)
    response = client.post("/api/admin/tokens", json={"actor_id": "scribe"})
    assert response.status_code == 200
    return {"Authorization": f"Bearer {response.json()['token']}"}


def _field_names(value) -> set[str]:
    if isinstance(value, dict):
        return set(value).union(*(_field_names(item) for item in value.values()))
    if isinstance(value, list):
        return set().union(*(_field_names(item) for item in value))
    return set()


def test_auth_required(client):
    assert client.get("/api/tasks").status_code == 401
    assert client.get("/api/health").status_code == 200


def test_unconfigured_site_vocabulary_is_absent_from_shared_interfaces(
    monkeypatch, tmp_path
):
    monkeypatch.delenv("RETINUE_INTERNAL_ROOT", raising=False)
    factory = make_session_factory(tmp_path / "unconfigured-site.db")
    with factory() as db:
        db.add(Actor(id="operator", kind="human", display_name="Operator"))
        db.add(
            User(
                username="operator",
                password_hash=hash_password("operator-pass-123"),
                role="admin",
                actor_id="operator",
            )
        )
        db.commit()
    unconfigured = TestClient(create_app(factory, data_dir=tmp_path))
    logged_in = unconfigured.post(
        "/api/auth/login",
        json={"username": "operator", "password": "operator-pass-123"},
    )
    assert logged_in.status_code == 200

    identity = unconfigured.get("/api/auth/me")
    onboarding = unconfigured.post(
        "/api/admin/onboarding/prepare",
        json={
            "actor_id": "new-agent",
            "display_name": "New Agent",
            "username": "new-agent-user",
            "password": "new-agent-pass-123",
        },
    )
    assert identity.status_code == onboarding.status_code == 200
    assert identity.json()["site_console"] is False

    site_vocabulary = "internal"
    route_paths = {
        getattr(route, "path", "") for route in unconfigured.app.routes
    }
    assert all(site_vocabulary not in path.lower() for path in route_paths)
    assert all(
        site_vocabulary not in field.lower()
        for response in (identity, onboarding)
        for field in _field_names(response.json())
    )


def test_login_rejects_bad_password(client):
    response = client.post(
        "/api/auth/login", json={"username": "owner", "password": "wrong-pass-123"}
    )
    assert response.status_code == 401


def test_login_throttles_one_account_but_allows_another_within_source_budget(client):
    for _ in range(3):
        response = client.post(
            "/api/auth/login",
            json={"username": "owner", "password": "wrong-pass-123"},
        )
        assert response.status_code == 401

    throttled = client.post(
        "/api/auth/login",
        json={"username": "owner", "password": "wrong-pass-123"},
    )
    assert throttled.status_code == 429
    assert throttled.json() == {"detail": "用户名或密码错误"}

    other_account = client.post(
        "/api/auth/login",
        json={"username": "reviewer", "password": "owner-pass-123"},
    )
    assert other_account.status_code == 200


def test_throttle_rejects_before_password_hashing(client, monkeypatch):
    for _ in range(3):
        client.post(
            "/api/auth/login",
            json={"username": "owner", "password": "wrong-pass-123"},
        )

    expensive_path_entered = False

    def observe_expensive_path(password: str, stored: str) -> bool:
        nonlocal expensive_path_entered
        expensive_path_entered = True
        return False

    monkeypatch.setattr(app_module, "verify_password", observe_expensive_path)
    response = client.post(
        "/api/auth/login",
        json={"username": "owner", "password": "wrong-pass-123"},
    )
    assert response.status_code == 429
    assert expensive_path_entered is False


def test_throttled_response_does_not_reveal_account_existence(client):
    responses = []
    for source, username in (
        ("synthetic-source-a", "owner"),
        ("synthetic-source-b", "missing-user"),
    ):
        source_client = TestClient(client.app, client=(source, 50000))
        for _ in range(3):
            failed = source_client.post(
                "/api/auth/login",
                json={"username": username, "password": "wrong-pass-123"},
            )
            assert failed.status_code == 401
        responses.append(
            source_client.post(
                "/api/auth/login",
                json={"username": username, "password": "wrong-pass-123"},
            )
        )

    assert [response.status_code for response in responses] == [429, 429]
    assert responses[0].json() == responses[1].json() == {
        "detail": "用户名或密码错误"
    }
    assert all(response.headers["retry-after"] == "1" for response in responses)


def test_demo_login_uses_the_password_login_source_throttle(client):
    for index in range(10):
        response = client.post(
            "/api/auth/login",
            json={"username": f"missing-user-{index}", "password": "wrong-pass-123"},
        )
        assert response.status_code == 401

    response = client.post("/api/auth/demo-login")
    assert response.status_code == 429
    assert response.headers["retry-after"] == "1"


def test_viewer_can_read_but_cannot_mutate(client):
    login(client)
    created_user = client.post(
        "/api/admin/users",
        json={
            "username": "observer",
            "password": "observer-pass-123",
            "role": "viewer",
            "display_name": "实盘观察席",
        },
    )
    assert created_user.status_code == 200
    assert client.post("/api/auth/logout").status_code == 200

    response = client.post(
        "/api/auth/login",
        json={"username": "observer", "password": "observer-pass-123"},
    )
    assert response.status_code == 200
    assert response.json()["role"] == "viewer"
    me = client.get("/api/auth/me")
    assert me.status_code == 200
    assert me.json()["readonly"] is True
    assert client.get("/api/tasks").status_code == 200

    created_task = client.post(
        "/api/tasks",
        json={"title": "观察席不应创建任务", "holder": "scribe"},
    )
    assert created_task.status_code == 403
    assert "只读" in created_task.json()["detail"]


def test_board_flow(client):
    login(client)
    created = client.post(
        "/api/tasks",
        json={"title": "写一篇稿子", "holder": "scribe", "priority": "high"},
    )
    assert created.status_code == 200
    task_id = created.json()["id"]
    assert created.json()["status"] == "queued"

    moved = client.post(
        f"/api/tasks/{task_id}/update", json={"status": "doing", "note": "开工"}
    )
    assert moved.status_code == 200
    assert moved.json()["status"] == "doing"
    assert len(moved.json()["chain"]) == 2

    illegal = client.post(
        f"/api/tasks/{task_id}/update", json={"status": "queued", "note": "回退"}
    )
    assert illegal.status_code == 422

    no_note = client.post(f"/api/tasks/{task_id}/update", json={"status": "done"})
    assert no_note.status_code == 422


def test_paginated_task_listing_returns_summaries_without_gaps(tmp_path):
    factory = make_session_factory(tmp_path / "paging.db")
    created_ids = []
    with factory() as db:
        db.add(Actor(id="operator", kind="human", display_name="Operator"))
        db.flush()
        for number in range(5):
            task = create_task(
                db,
                title=f"Page task {number}",
                created_by="operator",
                holder="operator",
            )
            created_ids.append(task.id)
        db.commit()

    listed_ids = []
    cursor = None
    with factory() as db:
        for _page_number in range(4):
            page = list_task_summaries(db, page_size=2, cursor=cursor)
            payload = [task_summary_to_dict(task) for task in page.items]
            assert all(
                "chain" not in task
                and "attempts" not in task
                and "reviews" not in task
                for task in payload
            )
            listed_ids.extend(task["id"] for task in payload)
            if not page.has_more:
                assert page.next_cursor is None
                break
            assert page.next_cursor
            cursor = page.next_cursor
        else:
            pytest.fail("task pagination cursor did not reach the final page")

    assert len(listed_ids) == len(set(listed_ids))
    assert set(listed_ids) == set(created_ids)


def test_summary_query_does_not_select_task_events(tmp_path):
    factory = make_session_factory(tmp_path / "summary.db")
    with factory() as db:
        db.add(Actor(id="operator", kind="human", display_name="Operator"))
        db.flush()
        create_task(
            db,
            title="Summary projection",
            created_by="operator",
            holder="operator",
        )
        db.commit()

    statements = []
    engine = factory.kw["bind"]

    def record_statement(_conn, _cursor, statement, _parameters, _context, _many):
        statements.append(statement)

    event.listen(engine, "before_cursor_execute", record_statement)
    try:
        with factory() as db:
            page = list_task_summaries(db, page_size=10)
            payload = [task_summary_to_dict(task) for task in page.items]
    finally:
        event.remove(engine, "before_cursor_execute", record_statement)

    assert len(statements) == 1
    assert "task_events" not in statements[0].lower()
    assert (
        payload
        and "chain" not in payload[0]
        and "attempts" not in payload[0]
        and "reviews" not in payload[0]
    )


def test_task_department_can_be_corrected_with_an_audit_event(client):
    login(client)
    created = client.post(
        "/api/tasks", json={"title": "待归类任务", "holder": "scribe"}
    )
    assert created.status_code == 200

    updated = client.post(
        f"/api/tasks/{created.json()['id']}/update",
        json={"dept": "研发", "note": "数据治理：归入研发"},
    )
    assert updated.status_code == 200
    assert updated.json()["dept"] == "研发"
    assert updated.json()["chain"][-1]["did"] == "数据治理：归入研发"


def test_live_drift_endpoint_is_read_only_and_detects_row_tampering(client):
    login(client)
    created = client.post(
        "/api/tasks",
        json={"title": "Audit target", "holder": "scribe", "priority": "low"},
    )
    task_id = created.json()["id"]
    endpoint = f"/api/tasks/{task_id}/drift"
    assert client.get(endpoint).json()["status"] == "in_sync"

    factory = client.app.state.session_factory
    with factory() as db:
        db.execute(update(Task).where(Task.id == task_id).values(priority="urgent"))
        db.commit()

    drifted = client.get(endpoint)
    assert drifted.status_code == 200
    assert drifted.json()["differences"] == {
        "priority": {"folded": "low", "stored": "urgent"}
    }

    # Restore the fixture row without adding evidence; the endpoint itself made no writes.
    with factory() as db:
        db.execute(update(Task).where(Task.id == task_id).values(priority="low"))
        db.commit()


def test_authenticated_task_artifact(client, tmp_path):
    login(client)
    created = client.post(
        "/api/tasks",
        json={"title": "交付一份备课包", "holder": "scribe"},
    )
    task_id = created.json()["id"]
    artifact_dir = tmp_path / "artifacts"
    artifact_dir.mkdir()
    artifact = artifact_dir / f"{task_id}.md"
    artifact.write_text("# 备课包\n\n可直接使用。", encoding="utf-8")

    delivered = client.get(f"/api/artifacts/{task_id}")
    assert delivered.status_code == 200
    assert delivered.headers["content-type"].startswith("text/markdown")
    assert "可直接使用" in delivered.text

    missing = client.get("/api/artifacts/task-does-not-exist")
    assert missing.status_code == 404


def test_holder_only_writes_for_agents(client):
    headers = agent_headers(client)
    mine = client.post(
        "/api/tasks",
        json={"title": "自领任务", "holder": "scribe"},
        headers=headers,
    )
    assert mine.status_code == 200
    task_id = mine.json()["id"]

    ok = client.post(
        f"/api/tasks/{task_id}/update",
        json={"status": "doing", "note": "开工"},
        headers=headers,
    )
    assert ok.status_code == 200

    others = client.post("/api/tasks", json={"title": "别人的卡", "holder": "owner"})
    other_id = others.json()["id"]
    forbidden = client.post(
        f"/api/tasks/{other_id}/update",
        json={"status": "doing", "note": "偷改"},
        headers=headers,
    )
    assert forbidden.status_code == 403


def test_bad_bearer_token_rejected(client):
    response = client.get(
        "/api/tasks", headers={"Authorization": f"Bearer {new_token()}"}
    )
    assert response.status_code == 401


def test_agent_token_cannot_create_nodes_by_reporting(client):
    headers = agent_headers(client)

    heartbeat = client.post(
        "/api/nodes/heartbeat",
        json={"id": "invented-node"},
        headers=headers,
    )
    inventory = client.post(
        "/api/nodes/runtimes",
        json={"node_id": "another-invented-node", "runtimes": []},
        headers=headers,
    )

    assert heartbeat.status_code == 403
    assert inventory.status_code == 403
    assert "node-scoped credential required" in heartbeat.json()["detail"]
    assert "node-scoped credential required" in inventory.json()["detail"]
    assert client.get("/api/nodes").json() == []


def test_admin_admission_is_explicit_but_does_not_impersonate_telemetry(client):
    login(client)

    admitted = client.post(
        "/api/admin/nodes",
        json={"node_id": "reserved-node", "label": "Reserved node"},
    )
    report = client.post(
        "/api/nodes/heartbeat",
        json={"id": "reserved-node"},
    )

    assert admitted.status_code == 200
    assert admitted.json()["admitted_by"] == "owner"
    assert admitted.json()["admitted_at"] is not None
    assert report.status_code == 403
    assert "node-scoped credential required" in report.json()["detail"]
    assert [node["id"] for node in client.get("/api/nodes").json()] == [
        "reserved-node"
    ]


def test_never_admitted_node_report_is_actionable_and_does_not_grow_roster(client):
    login(client)
    token = new_token("rnn")
    with client.app.state.session_factory() as db:
        db.add(
            NodeToken(
                token_hash=hash_token(token),
                node_id="unadmitted-node",
                label="synthetic-orphan-token",
            )
        )
        db.commit()

    response = client.post(
        "/api/nodes/heartbeat",
        json={"id": "unadmitted-node"},
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 403
    assert response.json()["detail"] == (
        "node is not admitted: unadmitted-node; ask an administrator to admit it"
    )
    assert client.get("/api/nodes").json() == []


def test_node_token_only_allows_bound_heartbeat(client):
    login(client)
    issued = client.post(
        "/api/admin/node-tokens",
        json={"node_id": "windows", "label": "windows-probe"},
    )
    assert issued.status_code == 200
    headers = {"Authorization": f"Bearer {issued.json()['token']}"}
    payload = {
        "id": "windows",
        "label": "Windows",
        "hostname": "workstation",
        "platform": "Windows-11",
        "uptime_seconds": 3600,
        "load": [],
        "disk": {"percent": 42.0},
        "memory": {"total": 16_000_000_000},
        "services": [{"unit": "Tailscale", "healthy": True}],
    }

    heartbeat = client.post(
        "/api/nodes/heartbeat", json=payload, headers=headers
    )
    assert heartbeat.status_code == 200
    assert heartbeat.json() == {"status": "ok"}

    wrong_node = client.post(
        "/api/nodes/heartbeat",
        json={**payload, "id": "forge"},
        headers=headers,
    )
    assert wrong_node.status_code == 403

    assert client.get("/api/tasks", headers=headers).status_code == 401
    assert client.post(
        "/api/tasks",
        json={"title": "节点令牌不得创建任务", "holder": "scribe"},
        headers=headers,
    ).status_code == 401

    nodes = client.get("/api/nodes").json()
    assert [node["id"] for node in nodes] == ["windows"]
    assert nodes[0]["membership_status"] == "admitted"
    assert nodes[0]["admitted_by"] == "owner"
    assert nodes[0]["admitted_at"] is not None
    tokens = client.get("/api/admin/node-tokens").json()
    assert tokens[0]["node_id"] == "windows"
    assert tokens[0]["last_used_at"] is not None


def test_retirement_preserves_heartbeat_and_inventory_history(client):
    login(client)
    issued = client.post(
        "/api/admin/node-tokens", json={"node_id": "archive-node"}
    )
    headers = {"Authorization": f"Bearer {issued.json()['token']}"}
    assert client.post(
        "/api/nodes/heartbeat",
        json={
            "id": "archive-node",
            "hostname": "synthetic-host",
            "platform": "SyntheticOS",
        },
        headers=headers,
    ).status_code == 200
    assert client.post(
        "/api/nodes/runtimes",
        json={
            "node_id": "archive-node",
            "runtimes": [{"runtime": "codex", "command": "codex"}],
        },
        headers=headers,
    ).status_code == 200

    retired = client.delete("/api/admin/nodes/archive-node")

    assert retired.status_code == 200
    assert retired.json()["retired_by"] == "owner"
    assert retired.json()["retired_at"] is not None
    assert client.get("/api/nodes").json() == []
    assert client.post(
        "/api/nodes/heartbeat", json={"id": "archive-node"}, headers=headers
    ).status_code == 401
    with client.app.state.session_factory() as db:
        node = db.get(Node, "archive-node")
        assert node is not None
        assert node.membership_status == "retired"
        assert node.hostname == "synthetic-host"
        assert node.platform == "SyntheticOS"
        assert node.retired_by == "owner"
        assert node.retired_at is not None
        runtime = db.query(NodeRuntime).filter_by(
            node_id="archive-node", runtime="codex"
        ).one()
        assert runtime.available is True
        stored_token = db.query(NodeToken).filter_by(node_id="archive-node").one()
        assert stored_token.disabled is True


def test_node_token_reports_runtime_inventory_without_transcript_access(client):
    login(client)
    assert client.post(
        "/api/actors/scribe/update", json={"runtime": "codex", "node": "windows"}
    ).status_code == 200
    issued = client.post(
        "/api/admin/node-tokens",
        json={"node_id": "windows", "label": "runtime-probe"},
    )
    assert issued.status_code == 200
    headers = {"Authorization": f"Bearer {issued.json()['token']}"}
    payload = {
        "node_id": "windows",
        "runtimes": [{"runtime": "codex", "command": "codex", "available": True}],
    }

    reported = client.post("/api/nodes/runtimes", json=payload, headers=headers)
    assert reported.status_code == 200
    assert reported.json()["runtimes"][0]["command"] == "codex"
    assert client.post(
        "/api/nodes/runtimes", json={**payload, "node_id": "forge"}, headers=headers
    ).status_code == 403
    assert client.get("/api/tasks", headers=headers).status_code == 401

    discovered = client.get("/api/agent-discovery")
    assert discovered.status_code == 200
    runtime = next(item for item in discovered.json()["runtimes"] if item["runtime"] == "codex")
    assert runtime["nodes"] == [{"id": "windows", "label": "windows", "detected_at": runtime["nodes"][0]["detected_at"]}]

def test_runtime_report_persists_the_source_the_probe_reported(client):
    """A runtime found off PATH must not be recorded as if it were on PATH."""
    login(client)
    issued = client.post(
        "/api/admin/node-tokens",
        json={"node_id": "windows", "label": "runtime-probe"},
    )
    assert issued.status_code == 200
    headers = {"Authorization": f"Bearer {issued.json()['token']}"}

    reported = client.post(
        "/api/nodes/runtimes",
        json={
            "node_id": "windows",
            "runtimes": [
                {"runtime": "kimi", "command": "kimi", "source": "well-known"},
                # An older probe omits the field; it only ever searched PATH.
                {"runtime": "codex", "command": "codex"},
            ],
        },
        headers=headers,
    )
    assert reported.status_code == 200

    stored = {
        item["runtime"]: item
        for item in client.get("/api/nodes/windows/runtimes").json()
    }
    assert stored["kimi"]["source"] == "well-known"
    assert stored["codex"]["source"] == "path"

    # A source this build has not heard of is recorded, not rejected: nodes run
    # mixed versions and a newer probe must not fail against an older server.
    assert client.post(
        "/api/nodes/runtimes",
        json={
            "node_id": "windows",
            "runtimes": [{"runtime": "kimi", "command": "kimi", "source": "future-source"}],
        },
        headers=headers,
    ).status_code == 200
    assert client.post(
        "/api/nodes/runtimes",
        json={
            "node_id": "windows",
            "runtimes": [{"runtime": "kimi", "command": "kimi", "source": "../etc"}],
        },
        headers=headers,
    ).status_code == 422


def test_dispatch_hall_flow(client):
    headers = agent_headers(client)
    posted = client.post(
        "/api/tasks", json={"title": "挂单任务", "open_dispatch": True}
    )
    assert posted.status_code == 200
    body = posted.json()
    assert body["open_dispatch"] is True
    assert body["holder"] == "owner"  # publisher keeps the baton
    task_id = body["id"]

    claimed = client.post(f"/api/tasks/{task_id}/claim", json={}, headers=headers)
    assert claimed.status_code == 200
    assert claimed.json()["holder"] == "scribe"
    assert claimed.json()["open_dispatch"] is False

    again = client.post(f"/api/tasks/{task_id}/claim", json={}, headers=headers)
    assert again.status_code == 409  # race/conflict semantics, not validation

    assigned = client.post("/api/tasks", json={"title": "直派任务", "holder": "scribe"})
    not_open = client.post(
        f"/api/tasks/{assigned.json()['id']}/claim", json={}, headers=headers
    )
    assert not_open.status_code == 409

    missing_holder = client.post("/api/tasks", json={"title": "无持棒非挂单"})
    assert missing_holder.status_code == 422


def test_progress_reporting(client):
    headers = agent_headers(client)
    task = client.post(
        "/api/tasks", json={"title": "进度任务", "holder": "scribe"}, headers=headers
    ).json()
    client.post(
        f"/api/tasks/{task['id']}/update",
        json={"status": "doing", "note": "开工"},
        headers=headers,
    )
    reported = client.post(
        f"/api/tasks/{task['id']}/update",
        json={"progress": 60, "note": "过半"},
        headers=headers,
    )
    assert reported.status_code == 200
    assert reported.json()["progress"] == 60

    out_of_range = client.post(
        f"/api/tasks/{task['id']}/update",
        json={"progress": 120, "note": "x"},
        headers=headers,
    )
    assert out_of_range.status_code == 422

    done = client.post(
        f"/api/tasks/{task['id']}/update",
        json={"status": "done", "note": "交付"},
        headers=headers,
    )
    assert done.json()["progress"] == 100


def test_metrics_roundtrip(client):
    headers = agent_headers(client)
    posted = client.post(
        "/api/metrics/ingest",
        json={
            "actor_id": "scribe",
            "date": "2026-07-25",
            "runtime": "claude-code",
            "input_tokens": 1000,
            "output_tokens": 250,
        },
        headers=headers,
    )
    assert posted.status_code == 200

    spoof = client.post(
        "/api/metrics/ingest",
        json={
            "actor_id": "owner",
            "date": "2026-07-25",
            "runtime": "x",
            "input_tokens": 1,
            "output_tokens": 1,
        },
        headers=headers,
    )
    assert spoof.status_code == 403

    summary = client.get("/api/metrics/summary?days=31")
    assert summary.status_code == 200
    actors = {row["actor_id"]: row for row in summary.json()["actors"]}
    assert actors["scribe"]["output"] == 250


def test_orientation_context_is_refreshable_and_sanitized(client):
    login(client)
    response = client.get("/api/orientation/context")
    assert response.status_code == 200
    body = response.json()
    assert body["schema_version"] == "retinue-internal-context/v1"
    assert any("/api/orientation/context" in item for item in body["bootstrap"])
    assert body["privacy_boundary"]["excluded"]
    assert 0 <= body["data_quality"]["score"] <= 100
    assert "rtn_" not in body["markdown"]
    assert "scribe" in {actor["id"] for actor in body["actors"]}


def test_onboarding_prepare_registers_actor_account_and_returns_context(client):
    login(client)
    prepared = client.post(
        "/api/admin/onboarding/prepare",
        json={
            "actor_id": "new-bot",
            "display_name": "新 BOT",
            "runtime": "hermes",
            "model": "demo-model",
            "node": "forge",
            "username": "new-bot-user",
            "password": "new-bot-pass-123",
            "label": "试点接入",
        },
    )
    assert prepared.status_code == 200
    body = prepared.json()
    assert body["status"] == "ready_for_profile"
    assert body["actor"]["id"] == "new-bot"
    assert body["account"]["actor_id"] == "new-bot"
    assert body["token"].startswith("rtn_")
    assert "rtn_" not in body["orientation"]["markdown"]

    duplicate = client.post(
        "/api/admin/onboarding/prepare",
        json={
            "actor_id": "new-bot",
            "display_name": "重复",
            "username": "new-bot-user-2",
            "password": "new-bot-pass-123",
        },
    )
    assert duplicate.status_code == 409

    agent_context = client.get(
        "/api/orientation/context",
        headers={"Authorization": f"Bearer {body['token']}"},
    )
    assert agent_context.status_code == 200
    assert agent_context.json()["audience"]["actor_id"] == "new-bot"

def test_data_catalog_reports_storage_contract_and_quality(client):
    login(client)
    response = client.get("/api/data-catalog")
    assert response.status_code == 200
    body = response.json()
    assert body["schema_version"] == "retinue-data-catalog/v1"
    assert body["storage_contract"]["operational"].startswith("SQLite")
    assert {layer["key"] for layer in body["layers"]} >= {"tasks", "actors", "skills", "knowledge", "sessions"}
    assert body["quality"]["checks"]
    check_keys = {item["key"] for item in body["quality"]["checks"]}
    assert {"human_contact", "session_index", "pipeline_templates"} <= check_keys
    assert body["privacy"]["excluded"]
