"""Lease grant, heartbeat, reclaim, fencing, retry, and human escalate."""

from __future__ import annotations

import datetime as dt

import pytest
from fastapi.testclient import TestClient

from server.app import create_app
from server.db import Actor, ApiToken, Task, User, WorkdirLock, make_session_factory
from server.engine import (
    Conflict,
    append_attempt,
    apply_reported_failure,
    claim_task,
    create_task,
    escalate_task,
    heartbeat_task,
    lease_settings,
    precheck_deliverable,
    reclaim_expired_leases,
    retry_plan,
    retry_task,
)
from server.security import hash_password, hash_token


ACTOR_BEARER = "lease-actor-bearer-for-tests"
NOW = dt.datetime(2026, 8, 12, 12, 0, tzinfo=dt.timezone.utc)


def _factory(tmp_path):
    factory = make_session_factory(tmp_path / "lease.db")
    with factory() as db:
        db.add_all(
            [
                Actor(id="publisher", kind="human", display_name="Publisher"),
                Actor(id="agent-one", kind="agent", display_name="Agent One"),
                Actor(id="agent-two", kind="agent", display_name="Agent Two"),
            ]
        )
        db.add(
            User(
                username="publisher",
                password_hash=hash_password("publisher-pass-123"),
                role="admin",
                actor_id="publisher",
            )
        )
        db.add(
            ApiToken(
                token_hash=hash_token(ACTOR_BEARER),
                actor_id="agent-one",
                label="lease worker",
            )
        )
        db.commit()
    return factory


def _open_card(db, title="Lease card", **kwargs):
    return create_task(
        db,
        title=title,
        created_by="publisher",
        holder="publisher",
        open_dispatch=True,
        **kwargs,
    )


def test_claim_grants_monotonic_term(tmp_path):
    factory = _factory(tmp_path)
    with factory() as db:
        task = _open_card(db)
        first = claim_task(db, task, claimant="agent-one", now=NOW)
        assert first.lease_term == 1
        assert first.holder == "agent-one"
        assert first.open_dispatch is False
        assert first.lease_expires_at is not None
        payload = first.events[-1].payload_json
        assert '"term": 1' in payload
        assert '"action": "grant"' in payload

        first.lease_expires_at = NOW
        first.open_dispatch = True
        first.holder = "publisher"
        db.flush()
        second = claim_task(
            db, first, claimant="agent-two", now=NOW + dt.timedelta(seconds=1)
        )
        assert second.lease_term == 2
        assert second.holder == "agent-two"


def test_heartbeat_renews_expiry_without_a_chain_event(tmp_path):
    factory = _factory(tmp_path)
    with factory() as db:
        task = claim_task(db, _open_card(db), claimant="agent-one", now=NOW)
        before = len(task.events)
        first_expiry = task.lease_expires_at
        later = NOW + dt.timedelta(seconds=30)
        heartbeat_task(
            db, task, who="agent-one", lease_term=1, now=later
        )
        assert len(task.events) == before
        assert task.lease_expires_at != first_expiry
        assert task.lease_heartbeat_at == later


def test_heartbeat_start_moves_queued_to_doing(tmp_path):
    factory = _factory(tmp_path)
    with factory() as db:
        task = claim_task(db, _open_card(db), claimant="agent-one", now=NOW)
        heartbeat_task(
            db,
            task,
            who="agent-one",
            lease_term=1,
            started=True,
            now=NOW + dt.timedelta(seconds=5),
        )
        assert task.status == "doing"
        assert task.lease_started_at is not None
        assert task.events[-1].to_status == "doing"


def test_expired_lease_is_reclaimed_to_the_hall(tmp_path):
    factory = _factory(tmp_path)
    with factory() as db:
        task = claim_task(db, _open_card(db), claimant="agent-one", now=NOW)
        heartbeat_task(
            db,
            task,
            who="agent-one",
            lease_term=1,
            started=True,
            now=NOW + dt.timedelta(seconds=1),
        )
        task.lease_expires_at = NOW + dt.timedelta(seconds=10)
        db.flush()
        results = reclaim_expired_leases(
            db, now=NOW + dt.timedelta(minutes=4)
        )
        assert results == [
            {"task_id": task.id, "action": "reclaim", "reason": "lost-heartbeat"}
        ]
        db.refresh(task)
        assert task.open_dispatch is True
        assert task.status == "blocked"
        assert task.holder == "publisher"
        assert task.retry_count == 1
        assert task.lease_expires_at is None
        assert any(
            "returned to the dispatch hall" in event.did for event in task.events
        )


def test_stale_term_write_is_fenced(tmp_path):
    factory = _factory(tmp_path)
    with factory() as db:
        task = claim_task(db, _open_card(db), claimant="agent-one", now=NOW)
        with pytest.raises(Conflict, match="stale lease term 99"):
            heartbeat_task(
                db,
                task,
                who="agent-one",
                lease_term=99,
                now=NOW + dt.timedelta(seconds=1),
            )
        with pytest.raises(Conflict, match="lease expired"):
            heartbeat_task(
                db,
                task,
                who="agent-one",
                lease_term=1,
                now=NOW + dt.timedelta(minutes=4),
            )
        task.lease_expires_at = NOW
        task.open_dispatch = True
        task.holder = "publisher"
        db.flush()
        claim_task(
            db, task, claimant="agent-two", now=NOW + dt.timedelta(minutes=5)
        )
        with pytest.raises(Conflict, match="stale lease term 1"):
            append_attempt(
                db,
                task,
                reporter_kind="actor",
                reporter_id="agent-one",
                duty=None,
                outcome="failed",
                started_at=NOW,
                ended_at=NOW + dt.timedelta(seconds=1),
                reason="zombie write",
                exit_status=1,
                idempotency_key="attempt:zombie-0001",
                lease_term=1,
                now=NOW + dt.timedelta(minutes=5, seconds=1),
            )


def test_retry_limit_escalates(tmp_path, monkeypatch):
    monkeypatch.setenv("RETINUE_LEASE_RETRY_LIMIT", "1")
    factory = _factory(tmp_path)
    with factory() as db:
        task = claim_task(db, _open_card(db), claimant="agent-one", now=NOW)
        heartbeat_task(
            db,
            task,
            who="agent-one",
            lease_term=1,
            started=True,
            now=NOW + dt.timedelta(seconds=1),
        )
        task.lease_expires_at = NOW
        db.flush()
        first = reclaim_expired_leases(db, now=NOW + dt.timedelta(minutes=4))
        assert first[0]["action"] == "reclaim"
        claimed = claim_task(
            db, task, claimant="agent-two", now=NOW + dt.timedelta(minutes=5)
        )
        heartbeat_task(
            db,
            claimed,
            who="agent-two",
            lease_term=claimed.lease_term,
            started=True,
            now=NOW + dt.timedelta(minutes=5, seconds=1),
        )
        claimed.lease_expires_at = NOW + dt.timedelta(minutes=5)
        db.flush()
        second = reclaim_expired_leases(
            db, now=NOW + dt.timedelta(minutes=10)
        )
        assert second[0]["action"] == "escalate"
        db.refresh(claimed)
        assert claimed.open_dispatch is False
        assert claimed.status == "blocked"
        assert any("retry limit reached" in event.did for event in claimed.events)


def test_human_escalate_and_retry(tmp_path):
    factory = _factory(tmp_path)
    with factory() as db:
        task = claim_task(db, _open_card(db), claimant="agent-one", now=NOW)
        heartbeat_task(
            db,
            task,
            who="agent-one",
            lease_term=1,
            started=True,
            now=NOW + dt.timedelta(seconds=1),
        )
        escalate_task(
            db,
            task,
            who="publisher",
            note="quota exhausted; parked for a human",
            reason="quota",
            failure_class="semantic",
            is_privileged=True,
            now=NOW + dt.timedelta(seconds=2),
        )
        assert task.status == "blocked"
        assert task.failure_class == "semantic"
        assert task.open_dispatch is False
        retry_task(
            db,
            task,
            who="publisher",
            note="quota restored; human retry",
            is_privileged=True,
            now=NOW + dt.timedelta(seconds=3),
        )
        assert task.status == "doing"
        assert task.retry_count == 0
        assert task.lease_term == 2
        assert task.failure_class is None


def test_semantic_failure_does_not_auto_retry(tmp_path):
    factory = _factory(tmp_path)
    with factory() as db:
        task = claim_task(db, _open_card(db), claimant="agent-one", now=NOW)
        heartbeat_task(
            db,
            task,
            who="agent-one",
            lease_term=1,
            started=True,
            now=NOW + dt.timedelta(seconds=1),
        )
        policy = apply_reported_failure(
            db,
            task,
            who="agent-one",
            failure_class="semantic",
            reason="quota",
            lease_term=1,
            is_privileged=False,
            now=NOW + dt.timedelta(seconds=2),
        )
        assert policy["action"] == "escalate"
        assert task.status == "blocked"
        assert task.open_dispatch is False


def test_precheck_failure_retries_then_escalates(tmp_path, monkeypatch):
    monkeypatch.setenv("RETINUE_LEASE_RETRY_LIMIT", "1")
    factory = _factory(tmp_path)
    with factory() as db:
        task = _open_card(
            db, acceptance=["renders a receipt", "keeps the chain"]
        )
        claim_task(db, task, claimant="agent-one", now=NOW)
        heartbeat_task(
            db,
            task,
            who="agent-one",
            lease_term=1,
            started=True,
            now=NOW + dt.timedelta(seconds=1),
        )
        first = precheck_deliverable(
            db,
            task,
            who="agent-one",
            lease_term=1,
            checks=[
                {"item": "renders a receipt", "passed": False, "feedback": "missing"},
                {"item": "keeps the chain", "passed": True, "feedback": ""},
            ],
            now=NOW + dt.timedelta(seconds=2),
        )
        assert first["passed"] is False
        assert first["action"] == "retry"
        second = precheck_deliverable(
            db,
            task,
            who="agent-one",
            lease_term=1,
            checks=[
                {"item": "renders a receipt", "passed": False, "feedback": "still missing"},
                {"item": "keeps the chain", "passed": True, "feedback": ""},
            ],
            now=NOW + dt.timedelta(seconds=3),
        )
        assert second["action"] == "escalate"
        assert task.status == "blocked"


def test_workdir_lock_refuses_a_second_live_run(tmp_path):
    factory = _factory(tmp_path)
    with factory() as db:
        first = claim_task(
            db, _open_card(db, title="One"), claimant="agent-one", now=NOW
        )
        heartbeat_task(
            db,
            first,
            who="agent-one",
            lease_term=1,
            started=True,
            workdir_key="shared-pack",
            now=NOW + dt.timedelta(seconds=1),
        )
        second = create_task(
            db,
            title="Two",
            created_by="publisher",
            holder="publisher",
            open_dispatch=True,
        )
        claim_task(
            db, second, claimant="agent-two", now=NOW + dt.timedelta(seconds=2)
        )
        with pytest.raises(Conflict, match="workdir is busy"):
            heartbeat_task(
                db,
                second,
                who="agent-two",
                lease_term=1,
                started=True,
                workdir_key="shared-pack",
                now=NOW + dt.timedelta(seconds=3),
            )
        assert db.get(WorkdirLock, "shared-pack").task_id == first.id


def test_retry_plan_keeps_session_unless_polluted(tmp_path):
    factory = _factory(tmp_path)
    with factory() as db:
        task = claim_task(db, _open_card(db), claimant="agent-one", now=NOW)
        clean, _ = append_attempt(
            db,
            task,
            reporter_kind="actor",
            reporter_id="agent-one",
            duty=None,
            outcome="failed",
            started_at=NOW,
            ended_at=NOW + dt.timedelta(seconds=2),
            reason="disconnect",
            exit_status=1,
            idempotency_key="attempt:clean-0001",
            lease_term=1,
            session_ref="sess-keep",
            checkpoint_ref="ckpt-1",
            failure_class="transient",
            workdir_key="pack-one",
            now=NOW + dt.timedelta(seconds=2),
        )
        plan = retry_plan(task, clean)
        assert plan["resume_session"] == "sess-keep"
        assert plan["new_session"] is False
        dirty, _ = append_attempt(
            db,
            task,
            reporter_kind="actor",
            reporter_id="agent-one",
            duty=None,
            outcome="failed",
            started_at=NOW,
            ended_at=NOW + dt.timedelta(seconds=3),
            reason="context overflow",
            exit_status=2,
            idempotency_key="attempt:dirty-0001",
            lease_term=1,
            session_ref="sess-keep",
            checkpoint_ref="ckpt-2",
            failure_class="semantic",
            workdir_key="pack-one",
            now=NOW + dt.timedelta(seconds=3),
        )
        dirty.failure_class = "context-overflow"
        polluted = retry_plan(task, dirty)
        assert polluted["new_session"] is True
        assert polluted["resume_session"] is None
        assert polluted["workdir_key"] == "pack-one"


def test_lease_settings_read_environment(monkeypatch):
    monkeypatch.setenv("RETINUE_LEASE_HEARTBEAT_SECONDS", "20")
    monkeypatch.setenv("RETINUE_LEASE_LOST_SECONDS", "90")
    monkeypatch.setenv("RETINUE_LEASE_START_TIMEOUT_SECONDS", "120")
    monkeypatch.setenv("RETINUE_LEASE_UNCLAIMED_SECONDS", "3600")
    monkeypatch.setenv("RETINUE_LEASE_RETRY_LIMIT", "5")
    settings = lease_settings()
    assert settings.heartbeat_seconds == 20
    assert settings.lost_seconds == 90
    assert settings.start_timeout_seconds == 120
    assert settings.unclaimed_seconds == 3600
    assert settings.retry_limit == 5


def test_http_claim_heartbeat_reclaim_and_briefing(tmp_path):
    factory = _factory(tmp_path)
    client = TestClient(create_app(factory))
    headers = {"Authorization": f"Bearer {ACTOR_BEARER}"}
    login = client.post(
        "/api/auth/login",
        json={"username": "publisher", "password": "publisher-pass-123"},
    )
    assert login.status_code == 200
    created = client.post(
        "/api/tasks",
        json={"title": "HTTP lease", "open_dispatch": True, "note": "posted"},
    )
    assert created.status_code == 200
    task_id = created.json()["id"]
    claimed = client.post(
        f"/api/tasks/{task_id}/claim", json={"note": "taking"}, headers=headers
    )
    assert claimed.status_code == 200
    body = claimed.json()
    assert body["lease"]["term"] == 1
    assert "start_briefing" in body
    assert "similar_tasks" in body["start_briefing"]
    assert "related_skills" in body["start_briefing"]
    beat = client.post(
        f"/api/tasks/{task_id}/heartbeat",
        json={"lease_term": 1, "started": True, "workdir_key": "http-pack"},
        headers=headers,
    )
    assert beat.status_code == 200
    assert beat.json()["status"] == "doing"
    assert beat.json()["lease"]["started_at"]
    fenced = client.post(
        f"/api/tasks/{task_id}/heartbeat",
        json={"lease_term": 99},
        headers=headers,
    )
    assert fenced.status_code == 409

    with factory() as db:
        row = db.get(Task, task_id)
        assert row is not None
        row.lease_expires_at = NOW
        db.commit()
    swept = client.post("/api/tasks/reclaim")
    assert swept.status_code == 200
    assert swept.json()["count"] >= 1
    listed = client.get("/api/tasks/ready").json()
    assert any(item["id"] == task_id for item in listed)
