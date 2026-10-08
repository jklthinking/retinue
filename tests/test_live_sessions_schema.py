from __future__ import annotations

import sqlite3

from sqlalchemy import inspect, select

from server.db import (
    Actor,
    LATEST_SCHEMA_VERSION,
    LiveSession,
    Node,
    SessionEndpointBinding,
    make_session_factory,
    migrate_database,
)


def test_live_identity_is_separate_from_endpoint_location(tmp_path):
    db_path = tmp_path / "live.db"
    factory = make_session_factory(db_path)
    with factory() as db:
        db.add(Actor(id="worker-1", kind="agent", display_name="Worker One"))
        db.add(Node(id="node-a", label="Node A"))
        live = LiveSession(
            id="live-20260902-001",
            actor_id="worker-1",
            runtime="codex",
            state="unknown",
            state_source="process",
            state_confidence=60,
        )
        db.add(live)
        db.flush()
        db.add(
            SessionEndpointBinding(
                live_session_id=live.id,
                node_id="node-a",
                backend="tmux",
                endpoint_id="tmux-synthetic",
                backend_generation="generation-one",
                display_location="work:1.2",
                binding_source="explicit",
                binding_confidence=100,
            )
        )
        db.commit()

    with factory() as db:
        stored = db.execute(select(LiveSession)).scalar_one()
        binding = db.execute(select(SessionEndpointBinding)).scalar_one()
        assert stored.actor_id == "worker-1"
        assert stored.runtime_session_id is None
        assert stored.state == "unknown"
        assert binding.live_session_id == stored.id
        assert binding.endpoint_id == "tmux-synthetic"
        assert binding.backend_generation == "generation-one"


def test_version_twenty_database_gains_live_session_tables(tmp_path):
    db_path = tmp_path / "version-twenty.db"
    make_session_factory(db_path)

    raw = sqlite3.connect(db_path)
    raw.execute("DROP TABLE session_endpoint_bindings")
    raw.execute("DROP TABLE live_sessions")
    raw.execute("DROP TABLE control_events")
    raw.execute("DROP TABLE control_envelopes")
    raw.execute("DROP TABLE session_endpoint_observations")
    raw.execute("ALTER TABLE nodes DROP COLUMN sessions_probed_at")
    raw.execute("UPDATE schema_version SET version = 20 WHERE id = 1")
    raw.commit()
    raw.close()

    result = migrate_database(db_path)
    upgraded = make_session_factory(db_path)

    assert (result.from_version, result.to_version) == (20, LATEST_SCHEMA_VERSION)
    assert LATEST_SCHEMA_VERSION == 27
    inspector = inspect(upgraded.kw["bind"])
    assert inspector.has_table("live_sessions")
    assert inspector.has_table("session_endpoint_bindings")
    assert inspector.has_table("session_endpoint_observations")
    assert inspector.has_table("control_envelopes")
    assert inspector.has_table("control_events")
    node_columns = {column["name"] for column in inspector.get_columns("nodes")}
    assert "sessions_probed_at" in node_columns
    live_columns = {column["name"] for column in inspector.get_columns("live_sessions")}
    assert {
        "actor_id",
        "runtime",
        "runtime_session_id",
        "task_id",
        "state",
        "state_source",
        "state_confidence",
        "capabilities_json",
    } <= live_columns
    binding_indexes = {
        index["name"]: index
        for index in inspector.get_indexes("session_endpoint_bindings")
    }
    assert binding_indexes["ux_session_endpoint_generation"]["unique"] == 1
    observation_indexes = {
        index["name"]: index
        for index in inspector.get_indexes("session_endpoint_observations")
    }
    assert observation_indexes["ux_session_observation_endpoint"]["unique"] == 1


def test_version_twenty_one_database_gains_observation_read_model(tmp_path):
    db_path = tmp_path / "version-twenty-one.db"
    make_session_factory(db_path)

    raw = sqlite3.connect(db_path)
    raw.execute("DROP TABLE control_events")
    raw.execute("DROP TABLE control_envelopes")
    raw.execute("DROP TABLE session_endpoint_observations")
    raw.execute("ALTER TABLE nodes DROP COLUMN sessions_probed_at")
    raw.execute("UPDATE schema_version SET version = 21 WHERE id = 1")
    raw.commit()
    raw.close()

    result = migrate_database(db_path)
    upgraded = make_session_factory(db_path)

    assert (result.from_version, result.to_version) == (21, 27)
    inspector = inspect(upgraded.kw["bind"])
    assert inspector.has_table("session_endpoint_observations")
    assert "sessions_probed_at" in {
        column["name"] for column in inspector.get_columns("nodes")
    }


def test_version_twenty_two_database_gains_control_envelopes(tmp_path):
    db_path = tmp_path / "version-twenty-two.db"
    make_session_factory(db_path)

    raw = sqlite3.connect(db_path)
    raw.execute("DROP TABLE control_events")
    raw.execute("DROP TABLE control_envelopes")
    raw.execute("ALTER TABLE session_endpoint_observations DROP COLUMN input_mode")
    raw.execute("UPDATE schema_version SET version = 22 WHERE id = 1")
    raw.commit()
    raw.close()

    result = migrate_database(db_path)
    upgraded = make_session_factory(db_path)

    assert (result.from_version, result.to_version) == (22, 27)
    inspector = inspect(upgraded.kw["bind"])
    assert inspector.has_table("control_envelopes")
    assert inspector.has_table("control_events")
    assert "input_mode" in {
        column["name"]
        for column in inspector.get_columns("session_endpoint_observations")
    }
    envelope_indexes = {
        index["name"]: index
        for index in inspector.get_indexes("control_envelopes")
    }
    assert envelope_indexes["ux_control_envelope_idempotency"]["unique"] == 1
