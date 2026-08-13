"""The task chain is sufficient evidence for current state and drift."""

from __future__ import annotations

import json

import pytest
from sqlalchemy import delete, update
import yaml

from core.protocol.task import (
    STATE_FIELDS,
    ProtocolError,
    audit_task_card,
    create_task as create_file_task,
    drift_report,
    fold_task_events,
    load_task,
    update_task as update_file_task,
)
from server.db import Actor, Task, TaskEvent, make_session_factory
from server.engine import (
    audit_stored_task,
    create_task,
    fold_stored_task,
    task_row_state,
    update_task,
)


def test_database_chain_folds_every_mutable_state_field_and_attributes_acceptance(
    tmp_path,
):
    factory = make_session_factory(tmp_path / "fold.db")
    with factory() as db:
        db.add_all(
            [
                Actor(id="owner", kind="human"),
                Actor(id="worker-one", kind="agent"),
                Actor(id="worker-two", kind="agent"),
            ]
        )
        db.flush()
        task = create_task(
            db,
            title="Foldable task",
            created_by="owner",
            holder="worker-one",
            dept="eng",
            priority="high",
            acceptance=["original criterion"],
            refs=["artifact:initial"],
        )
        update_task(
            db,
            task,
            who="owner",
            is_privileged=True,
            dept="research",
            priority="low",
            acceptance=["revised criterion"],
            refs=["artifact:revision"],
            note="revise task definition",
        )
        update_task(
            db,
            task,
            who="worker-one",
            is_privileged=False,
            status="doing",
            progress=35,
            note="start work",
        )
        update_task(
            db,
            task,
            who="worker-one",
            is_privileged=False,
            status="blocked",
            blocked_reason="waiting for review",
            note="record blocker",
        )
        update_task(
            db,
            task,
            who="owner",
            is_privileged=True,
            status="doing",
            holder="worker-two",
            progress=70,
            note="resume with new holder",
        )
        update_task(
            db,
            task,
            who="worker-two",
            is_privileged=False,
            status="done",
            note="acceptance verified",
        )

        folded = fold_stored_task(task)
        assert folded.reconstructible is True
        assert folded.history_complete is True
        assert set(folded.state) == set(STATE_FIELDS)
        assert folded.state == task_row_state(task)
        assert audit_stored_task(task)["status"] == "in_sync"

        acceptance_event = next(
            event
            for event in task.events
            if "acceptance" in json.loads(event.payload_json)["changes"]
            and event.seq > 1
        )
        change = json.loads(acceptance_event.payload_json)["changes"]["acceptance"]
        assert acceptance_event.who == "owner"
        assert acceptance_event.at.endswith("Z")
        assert change == {
            "before": ["original criterion"],
            "after": ["revised criterion"],
        }


def test_drift_report_detects_row_changed_behind_the_chain(tmp_path):
    factory = make_session_factory(tmp_path / "drift.db")
    with factory() as db:
        db.add(Actor(id="owner", kind="human"))
        db.flush()
        task = create_task(
            db,
            title="Drift target",
            created_by="owner",
            holder="owner",
            priority="low",
        )
        db.commit()
        task_id = task.id

    with factory() as db:
        db.execute(update(Task).where(Task.id == task_id).values(priority="urgent"))
        db.commit()

    with factory() as db:
        report = audit_stored_task(db.get(Task, task_id))
        assert report["status"] == "drift"
        assert report["in_sync"] is False
        assert report["differences"] == {
            "priority": {"folded": "low", "stored": "urgent"}
        }

    # Keep the shared suite invariant meaningful after this deliberate tamper.
    with factory() as db:
        db.execute(update(Task).where(Task.id == task_id).values(priority="low"))
        db.commit()


def test_legacy_chain_reports_partial_reconstruction_without_guessing():
    events = [
        {
            "who": "owner",
            "did": "created",
            "at": "2026-08-01T00:00:00.000000Z",
            "from_status": None,
            "to_status": "queued",
            "from_holder": None,
            "to_holder": "worker",
        },
        {
            "who": "worker",
            "did": "started",
            "at": "2026-08-01T00:01:00.000000Z",
            "from_status": "queued",
            "to_status": "doing",
            "from_holder": "worker",
            "to_holder": "worker",
        },
    ]

    folded = fold_task_events(events)
    assert folded.completeness == "partial"
    assert folded.reconstructible is False
    assert folded.history_complete is False
    assert folded.state == {"holder": "worker", "status": "doing"}
    assert set(folded.unknown_fields) == {
        "priority",
        "acceptance",
        "dept",
        "refs",
        "progress",
        "blocked_reason",
    }
    report = drift_report(folded, {"holder": "worker", "status": "doing"})
    assert report["status"] == "partial"
    assert report["in_sync"] is False


def test_file_mode_chain_uses_the_same_complete_fold(tmp_path):
    path = create_file_task(
        tmp_path,
        task_id="task-20260801-001",
        title="Portable fold",
        created_by="owner",
        holder="worker-one",
        dept="eng",
        priority="high",
        acceptance=["original criterion"],
    )
    update_file_task(
        path,
        dept="research",
        priority="low",
        acceptance=["revised criterion"],
        refs=["artifact:revision"],
        note="revise task definition",
        who="owner",
    )
    update_file_task(
        path,
        status="doing",
        progress=45,
        note="start work",
        who="worker-one",
    )
    update_file_task(
        path,
        status="blocked",
        blocked_reason="waiting for review",
        note="record blocker",
        who="worker-one",
    )
    task = update_file_task(
        path,
        status="doing",
        holder="worker-two",
        progress=75,
        note="resume with new holder",
        who="worker-one",
    )

    report = audit_task_card(task)
    assert report["status"] == "in_sync"
    assert set(report["fold"]["state"]) == set(STATE_FIELDS)


def test_updating_a_legacy_file_card_does_not_invent_omitted_old_values(tmp_path):
    path = tmp_path / "task-20260801-003.yaml"
    path.write_text(
        yaml.safe_dump(
            {
                "id": "task-20260801-003",
                "title": "Legacy portable card",
                "created_by": "owner",
                "status": "queued",
                "holder": "worker",
                "chain": [
                    {
                        "who": "owner",
                        "did": "created",
                        "at": "2026-08-01T00:00:00.000000Z",
                        "from_status": None,
                        "to_status": "queued",
                        "from_holder": None,
                        "to_holder": "worker",
                    }
                ],
                "refs": [],
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )

    updated = update_file_task(
        path,
        status="doing",
        note="start legacy card",
        who="worker",
    )
    assert {"priority", "acceptance", "progress"}.isdisjoint(updated)
    assert set(updated["chain"][-1]["payload"]["changes"]) == {"status"}
    report = audit_task_card(updated)
    assert report["status"] == "partial"
    assert {"priority", "acceptance", "progress"} <= set(
        report["fold"]["unknown_fields"]
    )


@pytest.mark.parametrize(
    "criterion",
    [
        "run --unsafe-option",
        "read /private/location",
        "api_key=not-for-audit-123456",
    ],
)
def test_state_payload_reuses_attempt_ledger_refusals(tmp_path, criterion):
    with pytest.raises(ProtocolError):
        create_file_task(
            tmp_path,
            task_id="task-20260801-002",
            title="Refusal boundary",
            created_by="owner",
            holder="worker",
            acceptance=[criterion],
        )
    assert not (tmp_path / "task-20260801-002.yaml").exists()


def test_audit_reports_a_deleted_note_only_event(tmp_path):
    """A removal the field comparison cannot see must still be reported.

    The fold catches a rewritten event because the before/after values stop matching. An
    event carrying only a note leaves the chain consistent when removed, so without a
    sequence check the audit would confirm a history that has a hole in it.
    """
    factory = make_session_factory(tmp_path / "gap.db")
    with factory() as db:
        db.add(Actor(id="owner", kind="human"))
        db.flush()
        task = create_task(
            db,
            title="Gap target",
            created_by="owner",
            holder="owner",
            priority="low",
        )
        update_task(db, task, who="owner", is_privileged=False, note="a plain note")
        update_task(db, task, who="owner", is_privileged=False, note="another note")
        db.commit()
        task_id = task.id
        assert audit_stored_task(task)["status"] == "in_sync"
        middle = sorted(event.seq for event in task.events)[1]

    with factory() as db:
        db.execute(
            delete(TaskEvent).where(
                TaskEvent.task_id == task_id, TaskEvent.seq == middle
            )
        )
        db.commit()

    with factory() as db:
        report = audit_stored_task(db.get(Task, task_id))
        assert report["missing_event_sequence"] == [middle]
        assert report["in_sync"] is False
        assert report["status"] == "invalid"
        # The point of the check: every field still agreed, which is why the hole was
        # invisible before.
        assert report["differences"] == {}
