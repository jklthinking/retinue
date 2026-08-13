"""Session sync contract: privacy, parsing, auth binding, and cursors."""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from adapters.exporters.sessions import collect_sessions
from server.app import create_app
from server.db import Actor, User, make_session_factory
from server.security import hash_password


def write_jsonl(path, records):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "\n".join(json.dumps(record) for record in records) + "\n",
        encoding="utf-8",
    )


def test_codex_session_export_respects_privacy_and_redacts(tmp_path):
    source = tmp_path / "sessions"
    transcript = source / "2026" / "07" / "sample.jsonl"
    write_jsonl(
        transcript,
        [
            {
                "type": "session_meta",
                "timestamp": "2026-07-30T08:00:00Z",
                "payload": {"id": "synthetic-session"},
            },
            {
                "type": "event_msg",
                "timestamp": "2026-07-30T08:01:00Z",
                "payload": {
                    "type": "user_message",
                    "message": "帮我整理 C:\\Users\\demo\\lesson.md，token=secret-value-123",
                },
            },
            {
                "type": "event_msg",
                "timestamp": "2026-07-30T08:02:00Z",
                "payload": {"type": "agent_message", "message": "已整理成三段课堂流程。"},
            },
        ],
    )
    before = transcript.read_bytes()

    metadata = collect_sessions(
        source, runtime="codex", agent_id="helper", privacy="metadata"
    )[0]
    assert metadata["title"].startswith("Codex 会话")
    assert metadata["summary"] == ""
    assert metadata["messages"] == []
    assert metadata["message_count"] == 2

    full = collect_sessions(
        source, runtime="codex", agent_id="helper", privacy="full"
    )[0]
    assert full["external_id"] == "synthetic-session"
    assert "[local-path]" in full["title"]
    assert "[redacted]" in full["title"]
    assert len(full["messages"]) == 2
    assert transcript.read_bytes() == before


def test_claude_session_export_reads_message_content(tmp_path):
    source = tmp_path / "projects"
    write_jsonl(
        source / "project" / "sample.jsonl",
        [
            {
                "type": "user",
                "timestamp": "2026-07-30T09:00:00Z",
                "sessionId": "claude-synthetic",
                "message": {"role": "user", "content": "生成一份分层练习"},
            },
            {
                "type": "assistant",
                "timestamp": "2026-07-30T09:01:00Z",
                "sessionId": "claude-synthetic",
                "message": {
                    "role": "assistant",
                    "content": [{"type": "text", "text": "已生成基础和挑战两档。"}],
                },
            },
        ],
    )

    row = collect_sessions(
        source, runtime="claude-code", agent_id="helper", privacy="summary"
    )[0]
    assert row["external_id"] == "claude-synthetic"
    assert row["title"] == "生成一份分层练习"
    assert "最近回复" in row["summary"]
    assert row["messages"] == []


def test_claude_session_export_ignores_subagent_transcripts(tmp_path):
    source = tmp_path / "projects"
    main = source / "project" / "claude-synthetic.jsonl"
    subagent = (
        source
        / "project"
        / "claude-synthetic"
        / "subagents"
        / "agent-child.jsonl"
    )
    write_jsonl(
        main,
        [
            {
                "type": "user",
                "timestamp": "2026-07-30T09:00:00Z",
                "sessionId": "claude-synthetic",
                "message": {"role": "user", "content": "main request"},
            }
        ],
    )
    write_jsonl(
        subagent,
        [
            {
                "type": "assistant",
                "timestamp": "2026-07-30T09:01:00Z",
                "sessionId": "claude-synthetic",
                "message": {"role": "assistant", "content": "child trace"},
            }
        ],
    )

    rows = collect_sessions(
        source, runtime="claude-code", agent_id="helper", privacy="summary"
    )

    assert len(rows) == 1
    assert rows[0]["external_id"] == "claude-synthetic"
    assert rows[0]["message_count"] == 1

def test_legacy_kimi_without_timestamps_uses_file_mtime(tmp_path):
    source = tmp_path / "kimi-legacy"
    transcript = source / "ses_aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee" / "context.jsonl"
    write_jsonl(
        transcript,
        [
            {"role": "user", "content": "legacy request"},
            {"role": "assistant", "content": "legacy response"},
        ],
    )

    row = collect_sessions(
        source, runtime="kimi-legacy", agent_id="helper", privacy="summary"
    )[0]

    assert row["external_id"] == "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
    assert row["started_at"] is not None
    assert row["updated_at"] is not None
    assert row["message_count"] == 2

def test_kimi_and_hermes_exporters_create_redacted_recaps(tmp_path):
    kimi_source = tmp_path / "kimi"
    kimi_session = (
        kimi_source
        / "wd_demo"
        / "ses_aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
    )
    write_jsonl(
        kimi_session / "agents" / "main" / "wire.jsonl",
        [
            {"type": "metadata", "created_at": "2026-07-30T10:00:00Z"},
            {
                "type": "context.append_message",
                "created_at": "2026-07-30T10:01:00Z",
                "message": {
                    "role": "user",
                    "content": [{"type": "text", "text": "整理组织周报"}],
                },
            },
            {
                "type": "context.append_message",
                "created_at": "2026-07-30T10:02:00Z",
                "message": {
                    "role": "assistant",
                    "content": [{"type": "text", "text": "已整理并列出三项行动。"}],
                },
            },
        ],
    )
    (kimi_session / "state.json").write_text(
        json.dumps(
            {
                "title": "组织周报",
                "createdAt": "2026-07-30T10:00:00Z",
                "updatedAt": "2026-07-30T10:02:00Z",
            }
        ),
        encoding="utf-8",
    )
    kimi = collect_sessions(
        kimi_source, runtime="kimi", agent_id="helper", privacy="summary"
    )[0]
    assert kimi["external_id"] == "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
    assert kimi["title"] == "组织周报"
    assert "三项行动" in kimi["summary"]

    hermes_source = tmp_path / "hermes"
    write_jsonl(
        hermes_source / "20260730_demo.jsonl",
        [
            {
                "role": "session_meta",
                "timestamp": "2026-07-30T11:00:00Z",
                "platform": "feishu",
            },
            {
                "role": "user",
                "timestamp": "2026-07-30T11:01:00Z",
                "content": "汇总会议结论",
            },
            {
                "role": "assistant",
                "timestamp": "2026-07-30T11:02:00Z",
                "content": "结论已归档。",
            },
        ],
    )
    hermes = collect_sessions(
        hermes_source, runtime="hermes", agent_id="helper", privacy="summary"
    )[0]
    assert hermes["external_id"] == "20260730_demo"
    assert hermes["message_count"] == 2
    assert "结论已归档" in hermes["summary"]


@pytest.fixture()
def client(tmp_path):
    factory = make_session_factory(tmp_path / "test.db")
    with factory() as db:
        db.add(Actor(id="owner", kind="human", display_name="负责人"))
        db.add(
            Actor(
                id="helper",
                kind="agent",
                display_name="备课助理",
                runtime="claude-code",
                node="desk",
            )
        )
        db.add(
            User(
                username="owner",
                password_hash=hash_password("owner-pass-123"),
                role="admin",
                actor_id="owner",
            )
        )
        db.commit()
    return TestClient(create_app(factory, data_dir=tmp_path))


def login(client: TestClient) -> None:
    response = client.post(
        "/api/auth/login",
        json={"username": "owner", "password": "owner-pass-123"},
    )
    assert response.status_code == 200


def agent_headers(client: TestClient) -> dict[str, str]:
    login(client)
    response = client.post("/api/admin/tokens", json={"actor_id": "helper"})
    assert response.status_code == 200
    return {"Authorization": f"Bearer {response.json()['token']}"}


def payload(**changes):
    body = {
        "actor_id": "helper",
        "runtime": "claude-code",
        "external_id": "synthetic-session",
        "title": "准备一节英语课",
        "summary": "",
        "privacy": "metadata",
        "cursor": 5,
        "message_count": 4,
        "messages": [],
        "started_at": "2026-07-30T09:00:00Z",
        "updated_at": "2026-07-30T09:12:00Z",
        "resume_capable": False,
    }
    body.update(changes)
    return body


def test_session_sync_is_actor_bound_idempotent_and_monotonic(client):
    headers = agent_headers(client)

    created = client.post("/api/sessions/sync", json=payload(), headers=headers)
    assert created.status_code == 200
    assert created.json()["sync_status"] == "created"
    assert created.json()["node"] == "desk"

    unchanged = client.post("/api/sessions/sync", json=payload(), headers=headers)
    assert unchanged.status_code == 200
    assert unchanged.json()["sync_status"] == "unchanged"

    conflict = client.post(
        "/api/sessions/sync",
        json=payload(title="同一游标却换了内容"),
        headers=headers,
    )
    assert conflict.status_code == 409

    upgraded = client.post(
        "/api/sessions/sync",
        json=payload(
            privacy="summary",
            summary="已完成课堂结构与互动设计。",
        ),
        headers=headers,
    )
    assert upgraded.status_code == 200
    assert upgraded.json()["sync_status"] == "updated"

    stale = client.post(
        "/api/sessions/sync",
        json=payload(cursor=4),
        headers=headers,
    )
    assert stale.status_code == 409

    spoofed = client.post(
        "/api/sessions/sync",
        json=payload(actor_id="owner", external_id="other"),
        headers=headers,
    )
    assert spoofed.status_code == 403


def test_session_list_filters_by_task_id(client):
    headers = agent_headers(client)
    login(client)
    task_ids = []
    for title in ("占位卡 甲", "占位卡 乙"):
        created = client.post("/api/tasks", json={"title": title, "holder": "helper"})
        assert created.status_code == 200
        task_ids.append(created.json()["id"])
    linked = client.post(
        "/api/sessions/sync",
        json=payload(external_id="linked", task_id=task_ids[0]),
        headers=headers,
    )
    assert linked.status_code == 200
    other = client.post(
        "/api/sessions/sync",
        json=payload(external_id="other", task_id=task_ids[1]),
        headers=headers,
    )
    assert other.status_code == 200
    free = client.post(
        "/api/sessions/sync",
        json=payload(external_id="free"),
        headers=headers,
    )
    assert free.status_code == 200

    listed = client.get(f"/api/sessions?task_id={task_ids[0]}")
    assert listed.status_code == 200
    rows = listed.json()
    assert len(rows) == 1
    assert rows[0]["task_id"] == task_ids[0]

    empty = client.get("/api/sessions?task_id=task-20990101-999")
    assert empty.status_code == 200
    assert empty.json() == []


def test_session_privacy_and_authenticated_reads(client):
    headers = agent_headers(client)
    invalid = client.post(
        "/api/sessions/sync",
        json=payload(summary="元数据模式不应携带摘要"),
        headers=headers,
    )
    assert invalid.status_code == 422

    full = client.post(
        "/api/sessions/sync",
        json=payload(
            privacy="full",
            summary="最近完成了课堂流程。",
            message_count=2,
            messages=[
                {
                    "role": "user",
                    "text": "请加入一个互动。",
                    "at": "2026-07-30T09:05:00Z",
                },
                {
                    "role": "assistant",
                    "text": "已加入站队选择互动。",
                    "at": "2026-07-30T09:06:00Z",
                },
            ],
        ),
        headers=headers,
    )
    assert full.status_code == 200
    session_id = full.json()["id"]

    listed = client.get("/api/sessions?q=课堂")
    assert listed.status_code == 200
    assert listed.json()[0]["messages"] == []

    detail = client.get(f"/api/sessions/{session_id}")
    assert detail.status_code == 200
    assert len(detail.json()["messages"]) == 2

    assert client.get("/api/sessions", headers={"Authorization": "Bearer bad"}).status_code == 401


def test_session_capture_can_export_to_obsidian_and_create_task(client):
    headers = agent_headers(client)
    synced = client.post("/api/sessions/sync", json=payload(), headers=headers)
    assert synced.status_code == 200
    session_id = synced.json()["id"]

    queued = client.post(
        f"/api/sessions/{session_id}/capture-obsidian",
        json={"title": "会话提取：英语课"},
        headers=headers,
    )
    assert queued.status_code == 200
    capture = queued.json()
    assert capture["status"] == "queued"
    assert "session_id:" in capture["markdown"]
    assert "仅同步会话元数据" in capture["markdown"]

    pending = client.get("/api/session-captures/pending", headers=headers)
    assert pending.status_code == 200
    assert [item["id"] for item in pending.json()] == [capture["id"]]

    exported = client.post(
        f"/api/session-captures/{capture['id']}/exported",
        json={"target_path": "00_Inbox/01_输入/会话归档/英语课.md"},
        headers=headers,
    )
    assert exported.status_code == 200
    assert exported.json()["status"] == "exported"

    created = client.post(
        f"/api/sessions/{session_id}/create-task",
        json={
            "title": "把英语课互动方案整理成任务卡",
            "dept": "教学",
            "acceptance": ["任务卡含互动方案与交付路径"],
        },
        headers=headers,
    )
    assert created.status_code == 200
    assert f"session:{session_id}" in created.json()["refs"]

    detail = client.get(f"/api/sessions/{session_id}", headers=headers)
    assert detail.json()["task_id"] == created.json()["id"]



def test_idle_summary_automatically_queues_idempotent_recap(client):
    headers = agent_headers(client)
    summary = "最近请求：整理周报\n最近回复：已形成三个结论和两个行动项。"
    synced = client.post(
        "/api/sessions/sync",
        json=payload(privacy="summary", summary=summary),
        headers=headers,
    )
    assert synced.status_code == 200

    pending = client.get("/api/session-captures/pending", headers=headers)
    assert pending.status_code == 200
    captures = pending.json()
    assert len(captures) == 1
    assert captures[0]["kind"] == "recap"
    assert "type: topic-archive" in captures[0]["markdown"]
    assert "## Recap" in captures[0]["markdown"]

    exported = client.post(
        f"/api/session-captures/{captures[0]['id']}/exported",
        json={"target_path": "40_Commons/话题归档/备课助理/2026-07/周报.md"},
        headers=headers,
    )
    assert exported.status_code == 200

    unchanged = client.post(
        "/api/sessions/sync",
        json=payload(privacy="summary", summary=summary),
        headers=headers,
    )
    assert unchanged.status_code == 200
    assert client.get("/api/session-captures/pending", headers=headers).json() == []
