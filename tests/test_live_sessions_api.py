"""Stage 2 live-session observation, binding, fencing, and visibility."""

from __future__ import annotations

import datetime as dt

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from server.app import create_app
from server.db import (
    Actor,
    ControlEnvelope,
    ControlEvent,
    LiveSession,
    SessionEndpointBinding,
    Task,
    User,
    make_session_factory,
    utcnow,
)
from server.security import hash_password


@pytest.fixture()
def live_env(tmp_path):
    factory = make_session_factory(tmp_path / "test.db")
    with factory() as db:
        db.add_all(
            [
                Actor(id="owner", kind="human", display_name="Owner"),
                Actor(id="worker-1", runtime="codex", display_name="Worker"),
                Actor(id="worker-2", runtime="codex", display_name="Other"),
                User(
                    username="owner",
                    password_hash=hash_password("owner-pass-123"),
                    role="admin",
                    actor_id="owner",
                ),
                Task(
                    id="task-20260902-001",
                    title="Bound work",
                    created_by="owner",
                    holder="worker-1",
                ),
                Task(
                    id="task-20260902-002",
                    title="Other work",
                    created_by="owner",
                    holder="worker-2",
                ),
            ]
        )
        db.commit()
    client = TestClient(create_app(factory, data_dir=tmp_path))
    assert client.post(
        "/api/auth/login",
        json={"username": "owner", "password": "owner-pass-123"},
    ).status_code == 200
    issued = client.post("/api/admin/node-tokens", json={"node_id": "node-d"})
    assert issued.status_code == 200
    headers = {"Authorization": f"Bearer {issued.json()['token']}"}
    return client, factory, headers


def _pane(**changes):
    pane = {
        "endpoint_id": "tmux-" + "a" * 24,
        "generation": "b" * 32,
        "backend": "tmux",
        "runtime": "codex",
        "actor_id": None,
        "live_session_id": None,
        "task_id": None,
        "explicit_binding": False,
        "binding_source": "process",
        "binding_confidence": 80,
        "occupant_verified": True,
        "state": "unknown",
        "state_source": "process",
        "state_confidence": 60,
        "command": "codex",
        "cwd_hint": "retinue-p0",
        "display_location": "work:0.1",
        "tmux": {
            "session_id": "$1",
            "window_id": "@2",
            "pane_id": "%4",
            "session_name": "work",
            "window_name": "agents",
        },
        "control_eligible": False,
    }
    pane.update(changes)
    return pane


def _report(*panes):
    return {
        "node_id": "node-d",
        "backend": "tmux",
        "server_id": "0123456789abcdef",
        "available": True,
        "status": "ok",
        "panes": list(panes),
        "ignored_rows": 0,
    }


def _explicit(**changes):
    values = {
        "actor_id": "worker-1",
        "live_session_id": "live-20260902-001",
        "task_id": "task-20260902-001",
        "explicit_binding": True,
        "binding_source": "explicit",
        "binding_confidence": 100,
        "occupant_verified": True,
        # Deliberately false: the Hub must ignore this diagnostic claim and
        # derive the authoritative eligibility itself.
        "control_eligible": False,
    }
    values.update(changes)
    return _pane(**values)


def test_unbound_observation_then_verified_binding(live_env):
    client, factory, headers = live_env

    observed = client.post(
        "/api/live-sessions/probe", json=_report(_pane()), headers=headers
    )
    assert observed.status_code == 200
    assert observed.json()["bound"] == 0
    row = client.get("/api/live-sessions").json()[0]
    assert row["binding_status"] == "unbound"
    assert row["control_eligible"] is False

    verified = client.post(
        "/api/live-sessions/probe", json=_report(_explicit()), headers=headers
    )
    assert verified.status_code == 200
    assert verified.json()["bound"] == 1
    row = client.get("/api/live-sessions").json()[0]
    assert row["binding_status"] == "bound"
    assert row["control_eligible"] is True
    assert row["bound_live_session_id"] == "live-20260902-001"

    with factory() as db:
        live = db.get(LiveSession, "live-20260902-001")
        assert live is not None
        assert (live.actor_id, live.runtime, live.task_id) == (
            "worker-1",
            "codex",
            "task-20260902-001",
        )
        binding = db.execute(
            select(SessionEndpointBinding).where(
                SessionEndpointBinding.invalidated_at.is_(None)
            )
        ).scalar_one()
        assert binding.backend_generation == "b" * 32


def test_wrong_node_token_and_absolute_paths_are_rejected(live_env):
    client, _factory, headers = live_env
    other = client.post("/api/admin/node-tokens", json={"node_id": "other-node"})
    other_headers = {"Authorization": f"Bearer {other.json()['token']}"}

    wrong = client.post(
        "/api/live-sessions/probe", json=_report(_pane()), headers=other_headers
    )
    assert wrong.status_code == 403

    private = _pane(cwd_hint="/synthetic/private/project")
    rejected = client.post(
        "/api/live-sessions/probe", json=_report(private), headers=headers
    )
    assert rejected.status_code == 422


def test_task_holder_mismatch_never_materializes_binding(live_env):
    client, factory, headers = live_env
    invalid_pane = _explicit(task_id="task-20260902-002")

    result = client.post(
        "/api/live-sessions/probe", json=_report(invalid_pane), headers=headers
    )
    assert result.status_code == 200
    assert result.json()["invalid"] == 1
    row = client.get("/api/live-sessions").json()[0]
    assert row["binding_status"] == "invalid"
    assert row["control_eligible"] is False
    with factory() as db:
        assert db.get(LiveSession, "live-20260902-001") is None


def test_generation_change_fences_old_binding_and_disappearance_is_stale(live_env):
    client, factory, headers = live_env
    assert client.post(
        "/api/live-sessions/probe", json=_report(_explicit()), headers=headers
    ).status_code == 200

    next_generation = _explicit(generation="c" * 32)
    changed = client.post(
        "/api/live-sessions/probe", json=_report(next_generation), headers=headers
    )
    assert changed.status_code == 200
    with factory() as db:
        bindings = list(
            db.execute(
                select(SessionEndpointBinding).order_by(SessionEndpointBinding.id)
            ).scalars()
        )
        assert len(bindings) == 2
        assert bindings[0].invalidated_at is not None
        assert bindings[1].invalidated_at is None
        assert bindings[1].backend_generation == "c" * 32

    missing = client.post(
        "/api/live-sessions/probe", json=_report(), headers=headers
    )
    assert missing.status_code == 200
    assert missing.json()["disappeared"] == 1
    row = client.get("/api/live-sessions").json()[0]
    assert row["binding_status"] == "stale"
    assert row["disappeared_at"] is not None
    assert row["control_eligible"] is False
    with factory() as db:
        assert db.get(LiveSession, "live-20260902-001").state == "disconnected"


def test_agent_can_only_read_own_bound_live_sessions(live_env):
    client, _factory, headers = live_env
    assert client.post(
        "/api/live-sessions/probe", json=_report(_explicit()), headers=headers
    ).status_code == 200
    worker_token = client.post(
        "/api/admin/tokens", json={"actor_id": "worker-1"}
    ).json()["token"]
    other_token = client.post(
        "/api/admin/tokens", json={"actor_id": "worker-2"}
    ).json()["token"]

    own = client.get(
        "/api/live-sessions", headers={"Authorization": f"Bearer {worker_token}"}
    )
    other = client.get(
        "/api/live-sessions", headers={"Authorization": f"Bearer {other_token}"}
    )
    assert [row["bound_live_session_id"] for row in own.json()] == [
        "live-20260902-001"
    ]
    assert other.json() == []


def test_tell_control_is_idempotent_leased_once_and_acknowledged(live_env):
    client, factory, headers = live_env
    pane = _explicit(input_mode="codex-prompt")
    assert client.post(
        "/api/live-sessions/probe", json=_report(pane), headers=headers
    ).status_code == 200
    request = {
        "verb": "tell",
        "message": "请汇报当前进度",
        "idempotency_key": "tell-stage3-001",
    }

    created = client.post(
        "/api/live-sessions/live-20260902-001/control", json=request
    )
    assert created.status_code == 200
    assert created.json()["created"] is True
    envelope_id = created.json()["id"]
    duplicate = client.post(
        "/api/live-sessions/live-20260902-001/control", json=request
    )
    assert duplicate.status_code == 200
    assert duplicate.json()["created"] is False
    conflict = client.post(
        "/api/live-sessions/live-20260902-001/control",
        json={**request, "message": "不同的内容"},
    )
    assert conflict.status_code == 409

    pulled = client.post(
        "/api/live-sessions/control/pull",
        json={"node_id": "node-d"},
        headers=headers,
    )
    assert pulled.status_code == 200
    controls = pulled.json()["controls"]
    assert len(controls) == 1
    assert controls[0]["id"] == envelope_id
    assert controls[0]["payload"] == {"message": "请汇报当前进度"}
    assert controls[0]["input_mode"] == "codex-prompt"
    assert client.post(
        "/api/live-sessions/control/pull",
        json={"node_id": "node-d"},
        headers=headers,
    ).json()["controls"] == []

    wrong_generation = client.post(
        "/api/live-sessions/control/ack",
        json={
            "node_id": "node-d",
            "envelope_id": envelope_id,
            "generation": "f" * 32,
            "outcome": "delivered",
        },
        headers=headers,
    )
    assert wrong_generation.status_code == 409
    ack_body = {
        "node_id": "node-d",
        "envelope_id": envelope_id,
        "generation": "b" * 32,
        "outcome": "delivered",
        "detail": "tmux paste-buffer delivered once",
    }
    acked = client.post(
        "/api/live-sessions/control/ack", json=ack_body, headers=headers
    )
    assert acked.status_code == 200
    assert acked.json()["status"] == "delivered"
    assert client.post(
        "/api/live-sessions/control/ack", json=ack_body, headers=headers
    ).status_code == 200
    with factory() as db:
        envelope = db.get(ControlEnvelope, envelope_id)
        assert envelope.attempts == 1
        assert envelope.status == "delivered"
        assert envelope.payload_json == "{}"
        events = list(
            db.execute(
                select(ControlEvent)
                .where(ControlEvent.envelope_id == envelope_id)
                .order_by(ControlEvent.seq)
            ).scalars()
        )
        assert [event.event_type for event in events] == [
            "created",
            "leased",
            "delivered",
        ]


def test_peek_is_owner_or_operator_only(live_env):
    client, _factory, headers = live_env
    assert client.post(
        "/api/live-sessions/probe", json=_report(_explicit()), headers=headers
    ).status_code == 200
    worker_token = client.post(
        "/api/admin/tokens", json={"actor_id": "worker-1"}
    ).json()["token"]
    other_token = client.post(
        "/api/admin/tokens", json={"actor_id": "worker-2"}
    ).json()["token"]
    body = {"verb": "peek", "lines": 12, "idempotency_key": "peek-stage3-001"}

    denied = client.post(
        "/api/live-sessions/live-20260902-001/control",
        json=body,
        headers={"Authorization": f"Bearer {other_token}"},
    )
    allowed = client.post(
        "/api/live-sessions/live-20260902-001/control",
        json=body,
        headers={"Authorization": f"Bearer {worker_token}"},
    )
    assert denied.status_code == 403
    assert allowed.status_code == 200


def test_generation_change_before_pull_fails_envelope_without_delivery(live_env):
    client, _factory, headers = live_env
    pane = _explicit(input_mode="codex-prompt")
    assert client.post(
        "/api/live-sessions/probe", json=_report(pane), headers=headers
    ).status_code == 200
    created = client.post(
        "/api/live-sessions/live-20260902-001/control",
        json={
            "verb": "tell",
            "message": "只可送达旧 generation",
            "idempotency_key": "tell-stage3-stale",
        },
    ).json()
    assert client.post(
        "/api/live-sessions/probe",
        json=_report(_explicit(input_mode="codex-prompt", generation="c" * 32)),
        headers=headers,
    ).status_code == 200

    pulled = client.post(
        "/api/live-sessions/control/pull",
        json={"node_id": "node-d"},
        headers=headers,
    )
    assert pulled.status_code == 200
    assert pulled.json()["controls"] == []
    status = client.get(f"/api/live-sessions/control/{created['id']}")
    assert status.status_code == 200
    assert status.json()["status"] == "failed"


def test_interrupt_is_owner_or_operator_only_and_has_short_expiry(live_env):
    client, _factory, headers = live_env
    assert client.post(
        "/api/live-sessions/probe", json=_report(_explicit()), headers=headers
    ).status_code == 200
    owner_token = client.post(
        "/api/admin/tokens", json={"actor_id": "worker-1"}
    ).json()["token"]
    other_token = client.post(
        "/api/admin/tokens", json={"actor_id": "worker-2"}
    ).json()["token"]
    body = {"verb": "interrupt", "idempotency_key": "interrupt-stage35-001"}

    denied = client.post(
        "/api/live-sessions/live-20260902-001/control",
        json=body,
        headers={"Authorization": f"Bearer {other_token}"},
    )
    allowed = client.post(
        "/api/live-sessions/live-20260902-001/control",
        json=body,
        headers={"Authorization": f"Bearer {owner_token}"},
    )
    assert denied.status_code == 403
    assert allowed.status_code == 200
    created = allowed.json()
    expiry = dt.datetime.fromisoformat(created["expires_at"])
    made = dt.datetime.fromisoformat(created["created_at"])
    assert (expiry - made).total_seconds() == 30
    pulled = client.post(
        "/api/live-sessions/control/pull",
        json={"node_id": "node-d"},
        headers=headers,
    ).json()["controls"]
    assert len(pulled) == 1
    assert pulled[0]["verb"] == "interrupt"
    assert pulled[0]["payload"] == {}


def test_queued_control_survives_hub_restart_and_completes(live_env, tmp_path):
    client, factory, headers = live_env
    assert client.post(
        "/api/live-sessions/probe",
        json=_report(_explicit(input_mode="codex-prompt")),
        headers=headers,
    ).status_code == 200
    created = client.post(
        "/api/live-sessions/live-20260902-001/control",
        json={
            "verb": "tell",
            "message": "restart-safe message",
            "idempotency_key": "tell-restart-001",
        },
    ).json()
    client.close()

    with TestClient(create_app(factory, data_dir=tmp_path)) as restarted:
        controls = restarted.post(
            "/api/live-sessions/control/pull",
            json={"node_id": "node-d"},
            headers=headers,
        ).json()["controls"]
        assert [item["id"] for item in controls] == [created["id"]]
        acknowledged = restarted.post(
            "/api/live-sessions/control/ack",
            json={
                "node_id": "node-d",
                "envelope_id": created["id"],
                "generation": "b" * 32,
                "outcome": "delivered",
                "detail": "delivered after Hub restart",
            },
            headers=headers,
        )
        assert acknowledged.status_code == 200
        assert acknowledged.json()["status"] == "delivered"

    with factory() as db:
        envelope = db.get(ControlEnvelope, created["id"])
        assert envelope is not None
        assert envelope.status == "delivered"
        assert envelope.attempts == 1
        assert envelope.payload_json == "{}"


def test_expired_control_is_terminal_and_never_delivered(live_env):
    client, factory, headers = live_env
    assert client.post(
        "/api/live-sessions/probe", json=_report(_explicit()), headers=headers
    ).status_code == 200
    created = client.post(
        "/api/live-sessions/live-20260902-001/control",
        json={
            "verb": "peek",
            "lines": 10,
            "idempotency_key": "peek-expired-001",
        },
    ).json()
    with factory() as db:
        envelope = db.get(ControlEnvelope, created["id"])
        assert envelope is not None
        envelope.expires_at = utcnow() - dt.timedelta(seconds=1)
        db.commit()

    pulled = client.post(
        "/api/live-sessions/control/pull",
        json={"node_id": "node-d"},
        headers=headers,
    )
    assert pulled.status_code == 200
    assert pulled.json()["controls"] == []
    status = client.get(f"/api/live-sessions/control/{created['id']}").json()
    assert status["status"] == "expired"
    assert status["attempts"] == 0
    with factory() as db:
        events = list(
            db.execute(
                select(ControlEvent)
                .where(ControlEvent.envelope_id == created["id"])
                .order_by(ControlEvent.seq)
            ).scalars()
        )
        assert [event.event_type for event in events] == ["created", "expired"]
        assert db.get(ControlEnvelope, created["id"]).payload_json == "{}"


def test_other_node_cannot_pull_or_ack_control(live_env):
    client, _factory, headers = live_env
    assert client.post(
        "/api/live-sessions/probe", json=_report(_explicit()), headers=headers
    ).status_code == 200
    created = client.post(
        "/api/live-sessions/live-20260902-001/control",
        json={
            "verb": "peek",
            "lines": 5,
            "idempotency_key": "peek-node-fence-001",
        },
    ).json()
    issued = client.post("/api/admin/node-tokens", json={"node_id": "other-node"})
    other_headers = {"Authorization": f"Bearer {issued.json()['token']}"}

    wrong_pull = client.post(
        "/api/live-sessions/control/pull",
        json={"node_id": "node-d"},
        headers=other_headers,
    )
    assert wrong_pull.status_code == 403
    assert len(
        client.post(
            "/api/live-sessions/control/pull",
            json={"node_id": "node-d"},
            headers=headers,
        ).json()["controls"]
    ) == 1
    wrong_ack = client.post(
        "/api/live-sessions/control/ack",
        json={
            "node_id": "other-node",
            "envelope_id": created["id"],
            "generation": "b" * 32,
            "outcome": "delivered",
            "result": "peek result",
        },
        headers=other_headers,
    )
    assert wrong_ack.status_code == 403


def test_conflicting_terminal_ack_is_rejected(live_env):
    client, _factory, headers = live_env
    assert client.post(
        "/api/live-sessions/probe", json=_report(_explicit()), headers=headers
    ).status_code == 200
    created = client.post(
        "/api/live-sessions/live-20260902-001/control",
        json={
            "verb": "peek",
            "lines": 5,
            "idempotency_key": "peek-ack-conflict-001",
        },
    ).json()
    assert len(
        client.post(
            "/api/live-sessions/control/pull",
            json={"node_id": "node-d"},
            headers=headers,
        ).json()["controls"]
    ) == 1
    ack = {
        "node_id": "node-d",
        "envelope_id": created["id"],
        "generation": "b" * 32,
        "outcome": "delivered",
        "result": "bounded output",
    }
    assert client.post(
        "/api/live-sessions/control/ack", json=ack, headers=headers
    ).status_code == 200
    conflict = client.post(
        "/api/live-sessions/control/ack",
        json={**ack, "outcome": "failed", "result": ""},
        headers=headers,
    )
    assert conflict.status_code == 409
