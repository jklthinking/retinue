"""Versioned schema migration and legacy-baselining coverage."""

from __future__ import annotations

import sqlite3

import pytest
from sqlalchemy import event, inspect, select
from sqlalchemy.engine import Engine

from server.db import (
    Actor,
    ApiToken,
    LATEST_SCHEMA_VERSION,
    MIGRATION_COMMAND,
    Node,
    SchemaVersion,
    Task,
    User,
    make_session_factory,
    migrate_database,
)
from server.engine import create_task
from server.main import main

LEGACY_TASKS_DDL = """
CREATE TABLE tasks (
    id VARCHAR(32) NOT NULL PRIMARY KEY,
    title VARCHAR(256) NOT NULL,
    created_by VARCHAR(64) NOT NULL,
    dept VARCHAR(64),
    priority VARCHAR(16) NOT NULL,
    status VARCHAR(16) NOT NULL,
    holder VARCHAR(64) NOT NULL,
    blocked_reason TEXT,
    next_holder VARCHAR(64),
    acceptance_json TEXT NOT NULL,
    refs_json TEXT NOT NULL,
    archived BOOLEAN NOT NULL,
    created_at DATETIME NOT NULL,
    updated_at DATETIME NOT NULL
)
"""

def _stored_version(factory) -> int:
    with factory() as db:
        return db.execute(
            select(SchemaVersion.version).where(SchemaVersion.id == 1)
        ).scalar_one()

def test_fresh_database_is_stamped_at_latest_version(tmp_path):
    factory = make_session_factory(tmp_path / "fresh.db")

    assert _stored_version(factory) == LATEST_SCHEMA_VERSION
    indexes = {
        index["name"]: index
        for index in inspect(factory.kw["bind"]).get_indexes("task_events")
    }
    assert indexes["ix_task_events_event_key"]["unique"] == 1
    attempt_columns = {
        column["name"]
        for column in inspect(factory.kw["bind"]).get_columns("task_attempts")
    }
    assert {
        "task_id",
        "seq",
        "attempt_key",
        "reporter_kind",
        "reporter_id",
        "duty",
        "outcome",
        "reason",
        "exit_status",
        "started_at",
        "ended_at",
        "reported_at",
    } <= attempt_columns
    attempt_indexes = {
        index["name"]: index
        for index in inspect(factory.kw["bind"]).get_indexes("task_attempts")
    }
    assert attempt_indexes["ux_task_attempts_task_seq"]["unique"] == 1
    assert attempt_indexes["ux_task_attempts_attempt_key"]["unique"] == 1
    dependency_columns = {
        column["name"]
        for column in inspect(factory.kw["bind"]).get_columns("task_dependencies")
    }
    assert {"dependent_id", "prerequisite_id", "kind", "created_at"} <= (
        dependency_columns
    )
    dependency_indexes = {
        index["name"]: index
        for index in inspect(factory.kw["bind"]).get_indexes("task_dependencies")
    }
    assert dependency_indexes["ux_task_dependencies_edge"]["unique"] == 1
    assert "ix_task_dependencies_prerequisite" in dependency_indexes
    actor_columns = {
        column["name"]
        for column in inspect(factory.kw["bind"]).get_columns("actors")
    }
    assert {"role", "goal"} <= actor_columns
    skill_columns = {
        column["name"]
        for column in inspect(factory.kw["bind"]).get_columns("skills")
    }
    assert {
        "source_kind",
        "source_snapshot_json",
        "imported_by",
        "imported_at",
    } <= skill_columns
    binding_columns = {
        column["name"]
        for column in inspect(factory.kw["bind"]).get_columns("skill_bindings")
    }
    assert {"actor_id", "skill_id", "enabled", "created_by"} <= binding_columns
    binding_indexes = {
        index["name"]: index
        for index in inspect(factory.kw["bind"]).get_indexes("skill_bindings")
    }
    assert binding_indexes["ux_skill_bindings_actor_skill"]["unique"] == 1
    event_indexes = {
        index["name"]: index
        for index in inspect(factory.kw["bind"]).get_indexes("skill_binding_events")
    }
    assert event_indexes["ux_skill_binding_events_actor_seq"]["unique"] == 1
    lease_columns = {
        column["name"] for column in inspect(factory.kw["bind"]).get_columns("tasks")
    }
    assert {
        "lease_term",
        "lease_expires_at",
        "lease_heartbeat_at",
        "retry_count",
        "workdir_key",
    } <= lease_columns
    attempt_lease = {
        column["name"]
        for column in inspect(factory.kw["bind"]).get_columns("task_attempts")
    }
    assert {"lease_term", "trigger_source", "session_ref", "checkpoint_ref"} <= (
        attempt_lease
    )
    lock_indexes = {
        index["name"]: index
        for index in inspect(factory.kw["bind"]).get_indexes("workdir_locks")
    }
    assert "ix_workdir_locks_task_id" in lock_indexes
    task_columns = {
        column["name"] for column in inspect(factory.kw["bind"]).get_columns("tasks")
    }
    assert "squad_id" in task_columns
    inspector = inspect(factory.kw["bind"])
    assert inspector.has_table("squads")
    assert inspector.has_table("squad_members")
    assert inspector.has_table("dispatch_schedules")
    assert inspector.has_table("dispatch_triggers")
    trigger_indexes = {
        index["name"]: index
        for index in inspector.get_indexes("dispatch_triggers")
    }
    assert trigger_indexes["ux_dispatch_triggers_source_key"]["unique"] == 1
    assert inspector.has_table("card_pipeline_templates")
    assert inspector.has_table("card_pipeline_instances")
    instance_indexes = {
        index["name"]: index
        for index in inspector.get_indexes("card_pipeline_instances")
    }
    assert instance_indexes["ux_card_pipeline_instances_key"]["unique"] == 1

def test_unversioned_legacy_database_is_baselined_only_by_explicit_migration(tmp_path):
    db_path = tmp_path / "legacy.db"
    raw = sqlite3.connect(db_path)
    raw.execute(LEGACY_TASKS_DDL)
    raw.commit()
    raw.close()

    with pytest.raises(RuntimeError, match="schema version 0"):
        make_session_factory(db_path)

    result = migrate_database(db_path)
    factory = make_session_factory(db_path)

    assert (result.from_version, result.to_version) == (0, LATEST_SCHEMA_VERSION)
    assert _stored_version(factory) == LATEST_SCHEMA_VERSION
    task_columns = {
        column["name"] for column in inspect(factory.kw["bind"]).get_columns("tasks")
    }
    assert {"progress", "open_dispatch", "pipeline_json", "pipeline_stage"} <= (
        task_columns
    )

def test_second_open_does_not_run_schema_changes(tmp_path):
    db_path = tmp_path / "current.db"
    make_session_factory(db_path)
    statements: list[str] = []

    def record_statement(_conn, _cursor, statement, _parameters, _context, _many):
        statements.append(statement.strip().upper())

    event.listen(Engine, "before_cursor_execute", record_statement)
    try:
        factory = make_session_factory(db_path)
    finally:
        event.remove(Engine, "before_cursor_execute", record_statement)

    assert _stored_version(factory) == LATEST_SCHEMA_VERSION
    assert not [
        statement
        for statement in statements
        if statement.startswith(("ALTER TABLE", "CREATE INDEX", "CREATE UNIQUE INDEX"))
    ]

def _make_version_eight_database(db_path) -> None:
    make_session_factory(db_path)
    raw = sqlite3.connect(db_path)
    for column in (
        "membership_status",
        "admitted_by",
        "admitted_at",
        "retired_by",
        "retired_at",
    ):
        raw.execute(f"ALTER TABLE nodes DROP COLUMN {column}")
    raw.execute("UPDATE schema_version SET version = 8 WHERE id = 1")
    raw.commit()
    raw.close()

def test_ordinary_open_refuses_one_version_behind_without_changing_it(tmp_path):
    db_path = tmp_path / "stale.db"
    _make_version_eight_database(db_path)

    with pytest.raises(RuntimeError) as caught:
        make_session_factory(db_path)

    message = str(caught.value)
    assert "schema version 8" in message
    assert f"required version {LATEST_SCHEMA_VERSION}" in message
    assert MIGRATION_COMMAND in message
    raw = sqlite3.connect(db_path)
    assert raw.execute("SELECT version FROM schema_version").fetchone() == (8,)
    columns = {row[1] for row in raw.execute("PRAGMA table_info(nodes)")}
    raw.close()
    assert "membership_status" not in columns

def test_migrate_command_reports_versions_and_is_idempotent(tmp_path, capsys):
    db_path = tmp_path / "retinue.db"
    _make_version_eight_database(db_path)

    assert main(["--data-dir", str(tmp_path), "migrate"]) == 0
    assert capsys.readouterr().out == (
        f"Database migration: from version 8 to version {LATEST_SCHEMA_VERSION} (schema upgraded).\n"
    )
    assert _stored_version(make_session_factory(db_path)) == LATEST_SCHEMA_VERSION

    assert main(["--data-dir", str(tmp_path), "migrate"]) == 0
    assert capsys.readouterr().out == (
        f"Database migration: from version {LATEST_SCHEMA_VERSION} to version {LATEST_SCHEMA_VERSION} (no changes).\n"
    )

def test_newer_database_still_refuses(tmp_path):
    db_path = tmp_path / "newer.db"
    make_session_factory(db_path)
    raw = sqlite3.connect(db_path)
    raw.execute(
        "UPDATE schema_version SET version = ? WHERE id = 1",
        (LATEST_SCHEMA_VERSION + 1,),
    )
    raw.commit()
    raw.close()

    with pytest.raises(RuntimeError) as caught:
        make_session_factory(db_path)

    assert str(LATEST_SCHEMA_VERSION + 1) in str(caught.value)
    assert f"supported version {LATEST_SCHEMA_VERSION}" in str(caught.value)

def test_version_seven_database_gains_actor_purpose_as_migration_eight(tmp_path):
    db_path = tmp_path / "version-seven.db"
    factory = make_session_factory(db_path)
    with factory() as db:
        db.add(Actor(id="legacy-agent", kind="agent", display_name="Legacy Agent"))
        db.commit()

    raw = sqlite3.connect(db_path)
    raw.execute("ALTER TABLE actors DROP COLUMN role")
    raw.execute("ALTER TABLE actors DROP COLUMN goal")
    raw.execute("UPDATE schema_version SET version = 7 WHERE id = 1")
    raw.commit()
    raw.close()

    result = migrate_database(db_path)
    upgraded = make_session_factory(db_path)

    assert (result.from_version, result.to_version) == (7, LATEST_SCHEMA_VERSION)
    assert _stored_version(upgraded) == LATEST_SCHEMA_VERSION
    with upgraded() as db:
        actor = db.get(Actor, "legacy-agent")
        assert actor is not None
        assert actor.role == actor.goal == ""

def test_version_eight_nodes_are_grandfathered_by_migration_nine(tmp_path):
    db_path = tmp_path / "version-eight.db"
    factory = make_session_factory(db_path)
    with factory() as db:
        db.add(
            Node(
                id="legacy-node",
                label="Legacy node",
                hostname="synthetic-host",
            )
        )
        db.commit()

    raw = sqlite3.connect(db_path)
    for column in (
        "membership_status",
        "admitted_by",
        "admitted_at",
        "retired_by",
        "retired_at",
    ):
        raw.execute(f"ALTER TABLE nodes DROP COLUMN {column}")
    raw.execute("UPDATE schema_version SET version = 8 WHERE id = 1")
    raw.commit()
    raw.close()

    result = migrate_database(db_path)
    upgraded = make_session_factory(db_path)

    assert (result.from_version, result.to_version) == (8, LATEST_SCHEMA_VERSION)
    assert _stored_version(upgraded) == LATEST_SCHEMA_VERSION
    with upgraded() as db:
        node = db.get(Node, "legacy-node")
        assert node is not None
        assert node.label == "Legacy node"
        assert node.hostname == "synthetic-host"
        assert node.membership_status == "admitted"
        assert node.admitted_by == "migration-v9"
        assert node.admitted_at is not None
        assert node.retired_by is None
        assert node.retired_at is None

def test_version_nine_tokens_gain_expires_at_as_migration_ten(tmp_path):
    db_path = tmp_path / "version-nine.db"
    factory = make_session_factory(db_path)
    with factory() as db:
        db.add(Actor(id="scribe", kind="agent", display_name="撰稿"))
        db.add(
            ApiToken(
                token_hash="hash-for-migration-ten",
                actor_id="scribe",
                label="pre-expiry",
            )
        )
        db.commit()

    raw = sqlite3.connect(db_path)
    raw.execute("ALTER TABLE api_tokens DROP COLUMN expires_at")
    raw.execute("UPDATE schema_version SET version = 9 WHERE id = 1")
    raw.commit()
    raw.close()

    result = migrate_database(db_path)
    upgraded = make_session_factory(db_path)

    assert (result.from_version, result.to_version) == (9, LATEST_SCHEMA_VERSION)
    assert _stored_version(upgraded) == LATEST_SCHEMA_VERSION == 20
    with upgraded() as db:
        token = db.execute(
            select(ApiToken).where(ApiToken.label == "pre-expiry")
        ).scalar_one()
        assert token.expires_at is None

def test_version_ten_tasks_gain_due_at_as_migration_eleven(tmp_path):
    db_path = tmp_path / "version-ten.db"
    factory = make_session_factory(db_path)
    with factory() as db:
        db.add(
            Actor(id="legacy-worker", kind="agent", display_name="Legacy Worker")
        )
        db.flush()
        create_task(
            db,
            title="Legacy card",
            created_by="legacy-worker",
            holder="legacy-worker",
        )
        db.commit()

    raw = sqlite3.connect(db_path)
    raw.execute("ALTER TABLE tasks DROP COLUMN due_at")
    raw.execute("UPDATE schema_version SET version = 10 WHERE id = 1")
    raw.commit()
    raw.close()

    result = migrate_database(db_path)
    upgraded = make_session_factory(db_path)

    assert (result.from_version, result.to_version) == (10, LATEST_SCHEMA_VERSION)
    assert _stored_version(upgraded) == 20 == LATEST_SCHEMA_VERSION
    with upgraded() as db:
        task = db.execute(select(Task)).scalars().one()
        assert task.due_at is None

def test_version_eleven_skills_gain_bindings_as_migration_twelve(tmp_path):
    db_path = tmp_path / "version-eleven.db"
    make_session_factory(db_path)

    raw = sqlite3.connect(db_path)
    for column in (
        "source_kind",
        "source_snapshot_json",
        "imported_by",
        "imported_at",
    ):
        raw.execute(f"ALTER TABLE skills DROP COLUMN {column}")
    raw.execute("DROP TABLE skill_bindings")
    raw.execute("DROP TABLE skill_binding_events")
    raw.execute("UPDATE schema_version SET version = 11 WHERE id = 1")
    raw.commit()
    raw.close()

    result = migrate_database(db_path)
    upgraded = make_session_factory(db_path)

    assert (result.from_version, result.to_version) == (11, LATEST_SCHEMA_VERSION)
    assert _stored_version(upgraded) == 20 == LATEST_SCHEMA_VERSION
    inspector = inspect(upgraded.kw["bind"])
    skill_columns = {column["name"] for column in inspector.get_columns("skills")}
    assert {
        "source_kind",
        "source_snapshot_json",
        "imported_by",
        "imported_at",
    } <= skill_columns
    binding_indexes = {
        index["name"]: index for index in inspector.get_indexes("skill_bindings")
    }
    assert binding_indexes["ux_skill_bindings_actor_skill"]["unique"] == 1
    event_indexes = {
        index["name"]: index
        for index in inspector.get_indexes("skill_binding_events")
    }
    assert event_indexes["ux_skill_binding_events_actor_seq"]["unique"] == 1

def test_version_twelve_tasks_gain_lease_as_migration_thirteen(tmp_path):
    db_path = tmp_path / "version-twelve.db"
    factory = make_session_factory(db_path)
    with factory() as db:
        db.add(Actor(id="lease-worker", kind="agent", display_name="Lease Worker"))
        db.flush()
        create_task(
            db,
            title="Pre-lease card",
            created_by="lease-worker",
            holder="lease-worker",
        )
        db.commit()

    raw = sqlite3.connect(db_path)
    raw.execute("DROP INDEX IF EXISTS ix_tasks_lease_expires_at")
    for column in (
        "lease_term",
        "lease_expires_at",
        "lease_heartbeat_at",
        "lease_claimed_at",
        "lease_started_at",
        "retry_count",
        "failure_class",
        "workdir_key",
        "hall_opened_at",
        "unclaimed_escalated",
    ):
        raw.execute(f"ALTER TABLE tasks DROP COLUMN {column}")
    for column in (
        "lease_term",
        "trigger_source",
        "session_ref",
        "checkpoint_ref",
        "failure_class",
        "workdir_key",
    ):
        raw.execute(f"ALTER TABLE task_attempts DROP COLUMN {column}")
    raw.execute("DROP TABLE workdir_locks")
    raw.execute("UPDATE schema_version SET version = 12 WHERE id = 1")
    raw.commit()
    raw.close()

    result = migrate_database(db_path)
    upgraded = make_session_factory(db_path)

    assert (result.from_version, result.to_version) == (12, LATEST_SCHEMA_VERSION)
    assert _stored_version(upgraded) == 20 == LATEST_SCHEMA_VERSION
    inspector = inspect(upgraded.kw["bind"])
    task_columns = {column["name"] for column in inspector.get_columns("tasks")}
    assert {"lease_term", "lease_expires_at", "retry_count", "hall_opened_at"} <= (
        task_columns
    )
    attempt_columns = {
        column["name"] for column in inspector.get_columns("task_attempts")
    }
    assert {"lease_term", "trigger_source", "checkpoint_ref"} <= attempt_columns
    with upgraded() as db:
        task = db.execute(select(Task)).scalars().one()
        assert task.lease_term == 0
        assert task.retry_count == 0
        assert task.unclaimed_escalated is False

def test_version_thirteen_databases_gain_seq_counters_as_migration_fourteen(tmp_path):
    db_path = tmp_path / "version-thirteen.db"
    factory = make_session_factory(db_path)
    with factory() as db:
        db.add(Actor(id="counter-worker", kind="agent", display_name="Counter Worker"))
        db.flush()
        first = create_task(
            db,
            title="Pre-counter card one",
            created_by="counter-worker",
            holder="counter-worker",
        )
        second = create_task(
            db,
            title="Pre-counter card two",
            created_by="counter-worker",
            holder="counter-worker",
        )
        db.commit()
        first_id, second_id = first.id, second.id

    raw = sqlite3.connect(db_path)
    raw.execute("DROP TABLE seq_counters")
    raw.execute("UPDATE schema_version SET version = 13 WHERE id = 1")
    raw.commit()
    raw.close()

    result = migrate_database(db_path)
    upgraded = make_session_factory(db_path)

    assert (result.from_version, result.to_version) == (13, LATEST_SCHEMA_VERSION)
    assert _stored_version(upgraded) == 20 == LATEST_SCHEMA_VERSION
    inspector = inspect(upgraded.kw["bind"])
    assert inspector.has_table("seq_counters")
    with upgraded() as db:
        # Numbering continues after the pre-upgrade rows instead of
        # restarting at 001 and colliding with them.
        third = create_task(
            db,
            title="Post-counter card",
            created_by="counter-worker",
            holder="counter-worker",
        )
        db.commit()
    prefix = first_id.rsplit("-", 1)[0]
    assert {first_id, second_id, third.id} == {
        f"{prefix}-001",
        f"{prefix}-002",
        f"{prefix}-003",
    }

def test_version_fourteen_gains_dispatch_v2_as_migration_fifteen(tmp_path):
    db_path = tmp_path / "version-fourteen.db"
    factory = make_session_factory(db_path)
    with factory() as db:
        db.add(Actor(id="route-worker", kind="agent", display_name="Route Worker"))
        db.flush()
        create_task(
            db,
            title="Pre-squad card",
            created_by="route-worker",
            holder="route-worker",
        )
        db.commit()

    raw = sqlite3.connect(db_path)
    raw.execute("DROP TABLE dispatch_triggers")
    raw.execute("DROP TABLE dispatch_schedules")
    raw.execute("DROP TABLE squad_members")
    raw.execute("DROP TABLE squads")
    raw.execute("ALTER TABLE tasks DROP COLUMN squad_id")
    raw.execute("UPDATE schema_version SET version = 14 WHERE id = 1")
    raw.commit()
    raw.close()

    result = migrate_database(db_path)
    upgraded = make_session_factory(db_path)

    assert (result.from_version, result.to_version) == (14, 20)
    assert _stored_version(upgraded) == 20 == LATEST_SCHEMA_VERSION
    assert (result.from_version, result.to_version) == (14, 20)
    assert _stored_version(upgraded) == 20 == LATEST_SCHEMA_VERSION
    inspector = inspect(upgraded.kw["bind"])
    assert inspector.has_table("squads")
    assert inspector.has_table("dispatch_schedules")
    assert inspector.has_table("dispatch_triggers")
    task_columns = {column["name"] for column in inspector.get_columns("tasks")}
    assert "squad_id" in task_columns
    with upgraded() as db:
        task = db.execute(select(Task)).scalars().first()
        assert task is not None
        assert task.squad_id is None

def test_version_fifteen_gains_card_pipelines_as_migration_seventeen(tmp_path):
    db_path = tmp_path / "version-fifteen.db"
    factory = make_session_factory(db_path)
    with factory() as db:
        db.add(Actor(id="chain-worker", kind="agent", display_name="Chain Worker"))
        db.flush()
        create_task(
            db,
            title="Pre-chain card",
            created_by="chain-worker",
            holder="chain-worker",
        )
        db.commit()

    raw = sqlite3.connect(db_path)
    raw.execute("DROP TABLE card_pipeline_instances")
    raw.execute("DROP TABLE card_pipeline_templates")
    raw.execute("UPDATE schema_version SET version = 15 WHERE id = 1")
    raw.commit()
    raw.close()

    result = migrate_database(db_path)
    upgraded = make_session_factory(db_path)

    assert (result.from_version, result.to_version) == (15, 20)
    assert _stored_version(upgraded) == 20 == LATEST_SCHEMA_VERSION
    assert (result.from_version, result.to_version) == (15, 20)
    assert _stored_version(upgraded) == 20 == LATEST_SCHEMA_VERSION
    inspector = inspect(upgraded.kw["bind"])
    assert inspector.has_table("card_pipeline_templates")
    assert inspector.has_table("card_pipeline_instances")
    template_columns = {
        column["name"] for column in inspector.get_columns("card_pipeline_templates")
    }
    assert {"id", "name", "spec_json", "created_by"} <= template_columns
    instance_columns = {
        column["name"] for column in inspector.get_columns("card_pipeline_instances")
    }
    assert {
        "id",
        "template_id",
        "instance_key",
        "status",
        "checkpoint_json",
    } <= instance_columns

def test_version_seventeen_gains_private_todos_as_migration_eighteen(tmp_path):
    db_path = tmp_path / "version-seventeen.db"
    factory = make_session_factory(db_path)
    with factory() as db:
        db.add(Actor(id="todo-owner", kind="human", display_name="Todo Owner"))
        db.add(
            User(
                username="todo-owner",
                password_hash="unused-hash",
                role="member",
                actor_id="todo-owner",
            )
        )
        db.commit()

    raw = sqlite3.connect(db_path)
    for table in (
        "todo_task_links",
        "reminder_deliveries",
        "todo_events",
        "todo_items",
        "todo_proposals",
    ):
        raw.execute(f"DROP TABLE {table}")
    raw.execute("ALTER TABLE users DROP COLUMN todo_propose_grants_json")
    raw.execute("UPDATE schema_version SET version = 17 WHERE id = 1")
    raw.commit()
    raw.close()

    result = migrate_database(db_path)
    upgraded = make_session_factory(db_path)

    assert (result.from_version, result.to_version) == (17, 20)
    assert _stored_version(upgraded) == 20 == LATEST_SCHEMA_VERSION
    assert (result.from_version, result.to_version) == (17, 20)
    assert _stored_version(upgraded) == 20 == LATEST_SCHEMA_VERSION
    inspector = inspect(upgraded.kw["bind"])
    for table in (
        "todo_proposals",
        "todo_items",
        "todo_events",
        "reminder_deliveries",
        "todo_task_links",
    ):
        assert inspector.has_table(table)
    user_columns = {column["name"] for column in inspector.get_columns("users")}
    assert "todo_propose_grants_json" in user_columns
    proposal_indexes = {
        index["name"] for index in inspector.get_indexes("todo_proposals")
    }
    assert "ux_todo_proposals_owner_dedup" in proposal_indexes
    with upgraded() as db:
        owner = db.execute(select(User).where(User.username == "todo-owner")).scalar_one()
        assert owner.todo_propose_grants_json == "[]"
