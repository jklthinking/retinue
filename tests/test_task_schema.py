from copy import deepcopy

import pytest
import yaml

from core.protocol.task import ProtocolError, create_task, load_task, update_task, validate_task


@pytest.fixture
def valid_task():
    return {
        "id": "task-20260719-001",
        "title": "Compile release notes",
        "created_by": "boss",
        "dept": "eng",
        "priority": "high",
        "acceptance": ["README renders"],
        "status": "doing",
        "holder": "writer-1",
        "blocked_reason": None,
        "chain": [
            {"who": "writer-1", "did": "claimed", "at": "2026-07-19T10:15+08:00"}
        ],
        "next": None,
        "refs": [],
    }


def test_valid_schema_boundary(valid_task):
    validate_task(valid_task)


@pytest.mark.parametrize("field", ["id", "title", "created_by", "status", "holder", "chain", "refs"])
def test_required_field_boundary(valid_task, field):
    task = deepcopy(valid_task)
    task.pop(field)
    with pytest.raises(ProtocolError, match="missing required fields"):
        validate_task(task)


@pytest.mark.parametrize(
    "mutation,message",
    [
        (lambda task: task.update(id="task-2026071-001"), "id must match"),
        (lambda task: task.update(holder="Writer_1"), "holder must use"),
        (lambda task: task.update(status="waiting"), "status must be"),
        (lambda task: task.update(priority="soon"), "priority must be"),
        (lambda task: task.update(acceptance=[""]), r"acceptance\[0\]"),
        (lambda task: task.update(chain={}), "chain must be a list"),
        (lambda task: task.update(refs=[3]), "refs must be a list"),
    ],
)
def test_invalid_schema_boundaries(valid_task, mutation, message):
    task = deepcopy(valid_task)
    mutation(task)
    with pytest.raises(ProtocolError, match=message):
        validate_task(task)


def test_blocked_requires_reason(valid_task):
    task = deepcopy(valid_task)
    task["status"] = "blocked"
    with pytest.raises(ProtocolError, match="blocked_reason"):
        validate_task(task)


def test_nonblocked_rejects_reason(valid_task):
    task = deepcopy(valid_task)
    task["blocked_reason"] = "stale"
    with pytest.raises(ProtocolError, match="must be null"):
        validate_task(task)


def test_update_appends_chain_without_rewriting_history(tmp_path):
    """Security invariant: docs/security.md#sec-3-append-only-chain-and-legal-transitions."""
    path = create_task(
        tmp_path,
        task_id="task-20260719-002",
        title="Review build",
        created_by="boss",
        holder="builder-1",
        at="2026-07-19T10:00+08:00",
    )
    before = deepcopy(load_task(path)["chain"])
    updated = update_task(
        path,
        status="doing",
        note="claimed task",
        at="2026-07-19T10:05+08:00",
    )
    assert updated["chain"][:-1] == before
    assert updated["chain"][-1]["from_status"] == "queued"
    assert updated["chain"][-1]["to_status"] == "doing"


def test_create_and_update_priority_and_acceptance(tmp_path):
    path = create_task(
        tmp_path,
        task_id="task-20260719-009",
        title="Protocol fields",
        created_by="boss",
        holder="builder-1",
        priority="urgent",
        acceptance=["lint passes"],
    )
    assert load_task(path)["priority"] == "urgent"
    updated = update_task(
        path,
        priority="low",
        acceptance=["tests pass", "receipt renders"],
        note="criteria revised",
    )
    assert updated["priority"] == "low"
    assert updated["acceptance"] == ["tests pass", "receipt renders"]


def test_legacy_card_without_mvp_fields_remains_valid(valid_task):
    valid_task.pop("priority")
    valid_task.pop("acceptance")
    validate_task(valid_task)


def test_note_only_update_appends_progress_event(tmp_path):
    path = create_task(
        tmp_path,
        task_id="task-20260719-005",
        title="Record progress",
        created_by="boss",
        holder="builder-1",
        at="2026-07-19T10:00+08:00",
    )
    updated = update_task(
        path,
        note="acceptance check passed",
        who="builder-1",
        at="2026-07-19T10:05+08:00",
    )
    assert len(updated["chain"]) == 2
    assert updated["chain"][-1] == {
        "who": "builder-1",
        "did": "acceptance check passed",
        "at": "2026-07-19T02:05:00.000000Z",
        "from_status": "queued",
        "to_status": "queued",
        "from_holder": "builder-1",
        "to_holder": "builder-1",
        "payload": {"state_version": 1, "changes": {}},
    }


def test_illegal_update_does_not_write(tmp_path):
    """Security negative case: docs/security.md#sec-3-append-only-chain-and-legal-transitions."""
    path = create_task(
        tmp_path,
        task_id="task-20260719-003",
        title="Review build",
        created_by="boss",
        holder="builder-1",
    )
    original = path.read_text()
    with pytest.raises(ProtocolError, match="illegal status transition"):
        update_task(path, status="done", note="skipped work")
    assert path.read_text() == original


def test_invalid_yaml_is_reported(tmp_path):
    path = tmp_path / "task-20260719-004.yaml"
    path.write_text("chain: [", encoding="utf-8")
    with pytest.raises(ProtocolError, match="cannot read"):
        load_task(path)
