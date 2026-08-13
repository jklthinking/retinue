"""Finish-to-start dependency graph and ready-work acceptance coverage."""

from __future__ import annotations

from copy import deepcopy

import pytest
import yaml
from sqlalchemy import event

from core.protocol.task import (
    ProtocolError,
    add_dependency,
    create_task as create_file_task,
    lint_path,
    load_task,
    ready_tasks as ready_file_tasks,
    update_task as update_file_task,
    validate_task,
)
from server.db import Actor, Task, make_session_factory
from server.engine import (
    add_task_dependency,
    create_task,
    list_ready_tasks,
    update_task,
)


def _server_graph(tmp_path):
    factory = make_session_factory(tmp_path / "dependencies.db")
    with factory() as db:
        db.add(Actor(id="operator", kind="human", display_name="Operator"))
        db.flush()
        prerequisite = create_task(
            db,
            title="Prepare source",
            created_by="operator",
            holder="operator",
        )
        dependent = create_task(
            db,
            title="Use source",
            created_by="operator",
            holder="operator",
            depends_on=[prerequisite.id],
        )
        db.commit()
        return factory, prerequisite.id, dependent.id


def test_ready_work_follows_prerequisite_completion(tmp_path):
    factory, prerequisite_id, dependent_id = _server_graph(tmp_path)

    with factory() as db:
        assert dependent_id not in {task.id for task in list_ready_tasks(db)}
        dependent = db.get(Task, dependent_id)
        assert dependent is not None
        with pytest.raises(ProtocolError, match="unfinished prerequisites"):
            update_task(
                db,
                dependent,
                who="operator",
                is_privileged=True,
                status="doing",
                note="must not start",
            )

    with factory() as db:
        prerequisite = db.get(Task, prerequisite_id)
        assert prerequisite is not None
        update_task(
            db,
            prerequisite,
            who="operator",
            is_privileged=True,
            status="doing",
            note="start prerequisite",
        )
        update_task(
            db,
            prerequisite,
            who="operator",
            is_privileged=True,
            status="done",
            note="finish prerequisite",
        )
        db.commit()

    with factory() as db:
        assert dependent_id in {task.id for task in list_ready_tasks(db)}


def test_cycle_is_refused_with_every_offending_card_named(tmp_path):
    factory = make_session_factory(tmp_path / "cycle.db")
    with factory() as db:
        db.add(Actor(id="operator", kind="human", display_name="Operator"))
        db.flush()
        cards = [
            create_task(
                db,
                title=f"Cycle card {index}",
                created_by="operator",
                holder="operator",
            )
            for index in range(3)
        ]
        add_task_dependency(
            db,
            cards[0],
            prerequisite_id=cards[1].id,
            kind="blocks",
            who="operator",
            is_privileged=True,
            note="first edge",
        )
        add_task_dependency(
            db,
            cards[1],
            prerequisite_id=cards[2].id,
            kind="blocks",
            who="operator",
            is_privileged=True,
            note="second edge",
        )

        with pytest.raises(ProtocolError) as rejected:
            add_task_dependency(
                db,
                cards[2],
                prerequisite_id=cards[0].id,
                kind="blocks",
                who="operator",
                is_privileged=True,
                note="closing edge",
            )

        message = str(rejected.value)
        assert "dependency cycle" in message
        assert all(card.id in message for card in cards)


def test_ready_query_never_loads_task_event_chains(tmp_path):
    factory, _prerequisite_id, _dependent_id = _server_graph(tmp_path)
    statements: list[str] = []
    engine = factory.kw["bind"]

    def record_statement(_conn, _cursor, statement, _parameters, _context, _many):
        statements.append(statement.lower())

    event.listen(engine, "before_cursor_execute", record_statement)
    try:
        with factory() as db:
            list_ready_tasks(db)
    finally:
        event.remove(engine, "before_cursor_execute", record_statement)

    assert statements
    assert not any("task_events" in statement for statement in statements)


def test_cancelling_a_prerequisite_with_active_dependents_is_refused(tmp_path):
    factory, prerequisite_id, dependent_id = _server_graph(tmp_path)
    with factory() as db:
        prerequisite = db.get(Task, prerequisite_id)
        assert prerequisite is not None
        with pytest.raises(ProtocolError) as rejected:
            update_task(
                db,
                prerequisite,
                who="operator",
                is_privileged=True,
                status="cancelled",
                note="cancel prerequisite",
            )

    message = str(rejected.value)
    assert prerequisite_id in message
    assert dependent_id in message


def test_file_mode_lint_checks_dependencies_and_accepts_older_cards(tmp_path):
    tasks = tmp_path / "tasks"
    prerequisite = create_file_task(
        tasks,
        task_id="task-20260809-001",
        title="File prerequisite",
        created_by="operator",
        holder="operator",
    )
    dependent = create_file_task(
        tasks,
        task_id="task-20260809-002",
        title="File dependent",
        created_by="operator",
        holder="operator",
        depends_on=["task-20260809-001"],
    )
    assert all(error is None for _path, error in lint_path(tasks))
    assert [card["id"] for card in ready_file_tasks(tasks)] == [
        "task-20260809-001"
    ]

    with pytest.raises(ProtocolError) as rejected:
        add_dependency(
            prerequisite,
            "task-20260809-002",
            note="would close cycle",
            who="operator",
        )
    assert "task-20260809-001" in str(rejected.value)
    assert "task-20260809-002" in str(rejected.value)

    update_file_task(prerequisite, status="doing", note="start")
    update_file_task(prerequisite, status="done", note="finish")
    assert {card["id"] for card in ready_file_tasks(tasks)} == {
        "task-20260809-002"
    }

    older = deepcopy(load_task(dependent))
    older.pop("depends_on")
    validate_task(older)

    invalid = load_task(dependent)
    invalid["depends_on"] = ["task-20260809-999"]
    dependent.write_text(
        yaml.safe_dump(invalid, sort_keys=False), encoding="utf-8"
    )
    errors = {path.name: error for path, error in lint_path(tasks)}
    assert "unknown card task-20260809-999" in errors[dependent.name]
